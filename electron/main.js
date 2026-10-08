// rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
// Created by LunarcatOwO (https://github.com/LunarcatOwO)
// Copyright (C) 2026 LunarcatOwO
// SPDX-License-Identifier: GPL-3.0-or-later
//
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License as published by the Free
// Software Foundation, either version 3 of the License, or (at your option)
// any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

// NFC Kiosk desktop app: boots the whole sign-in system and shows it.
//
// On launch, in order, with each step shown on screen:
//   1. the MariaDB database: checked, and started if it's on this Pi and down
//   2. the Python kiosk (python -m nfc_login --web-ui PORT) from the
//      rpi-login-system install, which brings up the NFC reader, keypad,
//      buzzer and the live web page on :8080
//   3. the kiosk screen, fullscreen at 800x480 (the 5 inch touchscreen)
// If any step fails, the screen says which and why, and it tries again.
// Quitting (Ctrl+Alt+Q, or System > 6 in the keypad's admin menu) stops the kiosk, and the
// database too if this app started it. System > 5 (Restart the kiosk app), and an update
// installed from the admin menu, restart the app: it stops the kiosk and starts a fresh copy
// of itself, which runs whatever code is now on disk. The database keeps running.
//
// Options (command line, or the matching environment variable):
//   --home PATH     the rpi-login-system folder   NFC_KIOSK_HOME (default ~/rpi-login-system)
//   --config PATH   config.toml to use            NFC_KIOSK_CONFIG (default HOME/config.toml)
//   --port N        local port for the screen     NFC_KIOSK_PORT (default 8081)
//   --simulate      no card reader or keypad (try it on a PC)
//   --windowed      an 800x480 window instead of fullscreen kiosk mode

const { app, BrowserWindow, globalShortcut, session } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const net = require("net");
const os = require("os");
const path = require("path");

function option(name, envName, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  if (i !== -1 && process.argv[i + 1]) return process.argv[i + 1];
  return process.env[envName] || fallback;
}

const HOME = path.resolve(option("home", "NFC_KIOSK_HOME", path.join(os.homedir(), "rpi-login-system")));
const CONFIG = option("config", "NFC_KIOSK_CONFIG", path.join(HOME, "config.toml"));
const PORT = Number(option("port", "NFC_KIOSK_PORT", "8081"));
const SIMULATE = process.argv.includes("--simulate");
const WINDOWED = process.argv.includes("--windowed");
const URL = `http://127.0.0.1:${PORT}/`;
const RETRY_SECONDS = 5;
const DB_WAIT_SECONDS = 30;

let win = null;
let backend = null;
// A restart hands this on, so the database is still stopped when the app finally quits.
let startedDatabase = process.env.NFC_KIOSK_STARTED_DB === "1";
let quitting = false;
let shutDown = false;
let restarting = false;
let logTail = [];
let steps = [];
let ready = false;

// Wayland (Raspberry Pi OS Bookworm and later) or X11, whichever the desktop runs.
app.commandLine.appendSwitch("ozone-platform-hint", "auto");

// ------------------------------------------------------------ status screen

const STEPS = [
  ["database", "Database"],
  ["python", "Kiosk program"],
  ["reader", "Card reader"],
  ["keypad", "Keypad"],
  ["buzzer", "Buzzer"],
  ["web", "Live web page (:8080)"],
  ["screen", "Kiosk screen"],
];

function resetSteps() {
  steps = STEPS.map(([id, label]) => ({ id, label, state: "wait", detail: "" }));
}

function step(id, state, detail = "") {
  const s = steps.find(x => x.id === id);
  if (s) Object.assign(s, { state, detail });
  render();
}

let status = { title: "Starting the kiosk…", message: "" };

function render() {
  if (!win || ready) return;
  const data = { ...status, steps, log: status.showLog ? logTail.slice(-6).join("\n") : "" };
  win.webContents.executeJavaScript(`window.setStatus && setStatus(${JSON.stringify(data)})`)
    .catch(() => {});
}

function showStatusPage() {
  ready = false;
  win.loadFile(path.join(__dirname, "loading.html")).then(render).catch(() => {});
}

function fail(id, detail, message) {
  step(id, "fail", detail);
  status = { title: "The kiosk couldn't start", message: `${message} Trying again in ${RETRY_SECONDS} s.`,
             showLog: true };
  render();
  setTimeout(() => { if (!quitting) boot(true); }, RETRY_SECONDS * 1000);
}

// ------------------------------------------------------------ 1. database

function run(cmd, args, timeoutMs) {
  return new Promise(resolve => {
    const child = spawn(cmd, args, { timeout: timeoutMs });
    let err = "";
    child.stderr.on("data", d => { err += d; });
    child.on("error", e => resolve({ code: -1, err: e.message }));
    child.on("exit", code => resolve({ code, err: err.trim() }));
  });
}

function databaseAddress() {
  // Just enough TOML to find [database] host/port; defaults match config.py.
  let host = "localhost", port = 3306, section = "";
  try {
    for (const raw of fs.readFileSync(CONFIG, "utf8").split("\n")) {
      const line = raw.replace(/#.*/, "").trim();
      const head = line.match(/^\[(.+)\]$/);
      if (head) { section = head[1].trim(); continue; }
      const kv = line.match(/^(\w+)\s*=\s*"?([^"]*)"?$/);
      if (section === "database" && kv) {
        if (kv[1] === "host") host = kv[2];
        if (kv[1] === "port") port = Number(kv[2]);
      }
    }
  } catch { /* no config file: defaults */ }
  return { host, port };
}

function reachable({ host, port }) {
  return new Promise(resolve => {
    const sock = net.connect({ host, port, timeout: 1500 });
    sock.on("connect", () => { sock.destroy(); resolve(true); });
    sock.on("error", () => resolve(false));
    sock.on("timeout", () => { sock.destroy(); resolve(false); });
  });
}

async function ensureDatabase() {
  const addr = databaseAddress();
  step("database", "run", `${addr.host}:${addr.port}`);
  if (await reachable(addr)) return step("database", "ok", "running");
  if (!["localhost", "127.0.0.1", "::1"].includes(addr.host)) {
    return fail("database", "not reachable", `The database at ${addr.host}:${addr.port} isn't answering.`);
  }
  step("database", "run", "starting MariaDB…");
  // Raspberry Pi OS lets the desktop user sudo without a password; -n never asks.
  const result = await run("sudo", ["-n", "systemctl", "start", "mariadb"], 60_000);
  if (result.code === 0) startedDatabase = true;
  else if (!logTail.some(l => l.startsWith("systemctl start mariadb"))) {
    logTail = logTail.concat(`systemctl start mariadb: ${result.err}`).slice(-12);
  }
  for (let i = 0; i < DB_WAIT_SECONDS; i++) {
    if (await reachable(addr)) return step("database", "ok", startedDatabase ? "started" : "running");
    await new Promise(r => setTimeout(r, 1000));
  }
  return fail("database", "won't start",
              "MariaDB isn't running. Check it with: sudo systemctl status mariadb.");
}

// ------------------------------------------------------------ 2. the Python kiosk

function python() {
  const venv = path.join(HOME, ".venv", "bin", "python");
  return fs.existsSync(venv) ? venv : "python3";
}

// Startup lines from nfc_login/__main__.py and web/server.py -> steps.
const PROGRESS = [
  [/active season: (.*)/, m => step("database", "ok", `season ${m[1]}`)],
  [/NFC reader: (.*)/, m => step("reader", "ok", m[1])],
  [/keypad: (\w+)/, m => step("keypad", m[1] === "ready" ? "ok" : "off", m[1])],
  [/buzzer: (\w+)/, m => step("buzzer", m[1] === "ready" ? "ok" : "off", m[1])],
  [/live page on (\S+)/, () => step("web", "ok", "port 8080")],
  [/web page not started on port (\d+): (.*)/, m => step("web", "warn", m[2])],
  [/live page: off/, () => step("web", "off", "off")],
  [/kiosk screen at/, () => step("screen", "run", "")],
];

// Commands from the kiosk on its own pipe (fd 3, NFC_KIOSK_CONTROL_FD), never from the
// log: the log also carries web page requests, which anyone on the network can word.
// 2.5 s first so the screen can be read.
const CONTROL = {
  close: () => setTimeout(() => app.quit(), 2500),     // System > 6 in the admin menu
  restart: () => setTimeout(restart, 2500),            // System > 5, or an update installed
};

function restart() {
  if (quitting) return;
  restarting = true;
  process.env.NFC_KIOSK_STARTED_DB = startedDatabase ? "1" : "";
  // relaunch() starts a new copy once this one has quit (before-quit stops the kiosk
  // first). An AppImage must be started through its own file, not the unpacked one.
  if (process.env.APPIMAGE) app.relaunch({ execPath: process.env.APPIMAGE, args: process.argv.slice(1) });
  else app.relaunch();
  app.quit();
}

function startBackend() {
  const args = ["-m", "nfc_login", "--web-ui", String(PORT)];
  if (fs.existsSync(CONFIG)) args.push("--config", CONFIG);
  if (SIMULATE) args.push("--simulate");
  step("python", "run", path.basename(python()));
  if (!fs.existsSync(path.join(HOME, "nfc_login"))) {
    return fail("python", "not found", `No rpi-login-system in ${HOME}. Use --home to point at it.`);
  }
  const child = spawn(python(), args, {
    cwd: HOME,
    env: { ...process.env, PYTHONUNBUFFERED: "1", NFC_KIOSK_CONTROL_FD: "3" },
    stdio: ["ignore", "pipe", "pipe", "pipe"],
  });
  backend = child;
  let control = "";
  child.stdio[3].on("data", chunk => {
    control += String(chunk);
    const lines = control.split("\n");
    control = lines.pop();
    for (const line of lines) {
      if (backend === child && Object.hasOwn(CONTROL, line)) CONTROL[line]();
    }
  });
  let buffer = "";
  const read = chunk => {
    process.stdout.write(chunk);
    buffer += String(chunk);
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) {
      if (!line.trim()) continue;
      logTail = logTail.concat(line).slice(-12);
      if (steps.find(s => s.id === "python").state === "run") step("python", "ok", "running");
      for (const [pattern, apply] of PROGRESS) {
        const m = line.match(pattern);
        if (m) apply(m);
      }
    }
  };
  child.stdout.on("data", read);
  child.stderr.on("data", read);
  child.on("error", err => logTail.push(`Couldn't start ${python()}: ${err.message}`));
  child.on("exit", code => {
    if (backend === child) backend = null;
    if (quitting) return;
    // Whatever was still starting is what broke (e.g. the reader not wired).
    const broken = steps.find(s => s.state === "run" || s.state === "wait") || steps[1];
    showStatusPage();
    fail(broken.id, `stopped (code ${code})`, "The kiosk program stopped.");
  });
  return true;
}

function answering() {
  return new Promise(resolve => {
    const req = http.get(URL + "api/kiosk", res => { res.resume(); resolve(res.statusCode === 200); });
    req.on("error", () => resolve(false));
    req.setTimeout(1000, () => { req.destroy(); resolve(false); });
  });
}

async function waitThenShow(child) {
  for (;;) {
    if (quitting || backend !== child) return;
    if (await answering()) break;
    await new Promise(r => setTimeout(r, 500));
  }
  step("screen", "ok");
  status = { title: "Starting the kiosk…", message: "" };
  await new Promise(r => setTimeout(r, 600));   // a moment to see everything ticked
  if (quitting || backend !== child) return;
  ready = true;
  win.loadURL(URL + (WINDOWED ? "" : "?kiosk=1"));
}

// ------------------------------------------------------------ boot and shutdown

async function boot(retry = false) {
  if (quitting) return;
  resetSteps();
  // On a retry the last error stays on screen until this attempt gets past it.
  status = retry ? { ...status, message: "Trying again…" } : { title: "Starting the kiosk…", message: "" };
  if (!retry) logTail = [];
  showStatusPage();
  await ensureDatabase();
  if (quitting || steps[0].state !== "ok") return;   // fail() already set up a retry
  if (startBackend() !== true) return;
  waitThenShow(backend);
}

function stopBackend() {
  return new Promise(resolve => {
    if (!backend) return resolve();
    const child = backend;
    const timer = setTimeout(() => { child.kill("SIGKILL"); resolve(); }, 5000);
    child.once("exit", () => { clearTimeout(timer); resolve(); });
    child.kill("SIGINT");   // lets Python release the GPIO pins
  });
}

async function shutdown() {
  quitting = true;
  // A restart leaves the database running for the new copy of the app.
  const stopDatabase = startedDatabase && !restarting;
  if (win) {
    ready = false;
    resetSteps();
    status = restarting
      ? { title: "Restarting the kiosk…", message: "Back in a few seconds." }
      : { title: "Shutting down…", message: "Stopping the kiosk" +
          (stopDatabase ? " and the database." : ".") };
    showStatusPage();
  }
  await stopBackend();
  if (stopDatabase) await run("sudo", ["-n", "systemctl", "stop", "mariadb"], 30_000);
}

function createWindow() {
  win = new BrowserWindow({
    width: 800,
    height: 480,
    fullscreen: !WINDOWED,
    kiosk: !WINDOWED,
    autoHideMenuBar: true,
    backgroundColor: "#101418",
    title: "NFC Sign In",
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  win.setMenu(null);
  // The window only ever shows the local kiosk page or the status page.
  win.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(URL) && !url.startsWith("file://")) event.preventDefault();
  });
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
}

app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((_wc, _perm, done) => done(false));
  globalShortcut.register("Control+Alt+Q", () => app.quit());
  createWindow();
  boot();
});

app.on("before-quit", event => {
  if (shutDown) return;
  event.preventDefault();
  if (quitting) return;
  shutdown().finally(() => { shutDown = true; app.quit(); });
});
app.on("window-all-closed", () => app.quit());
for (const sig of ["SIGTERM", "SIGINT"]) process.on(sig, () => app.quit());
