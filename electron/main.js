// NFC Kiosk desktop app.
//
// Starts the Python kiosk (python -m nfc_login --web-ui PORT) from the
// rpi-login-system install, waits until it answers, then shows its screen in
// a fullscreen kiosk window sized for the 5 inch 800x480 touchscreen. If the
// Python side stops, the window says why and it's restarted after a pause.
//
// Options (command line, or the matching environment variable):
//   --home PATH     the rpi-login-system folder   NFC_KIOSK_HOME (default ~/rpi-login-system)
//   --config PATH   config.toml to use            NFC_KIOSK_CONFIG (default HOME/config.toml)
//   --port N        local port for the screen     NFC_KIOSK_PORT (default 8081)
//   --simulate      no card reader or keypad (try it on a PC)
//   --windowed      an 800x480 window instead of fullscreen kiosk mode
//
// Ctrl+Alt+Q quits (kiosk mode has no close button).

const { app, BrowserWindow, globalShortcut, session } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
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
const RESTART_SECONDS = 5;

let win = null;
let backend = null;
let quitting = false;
let logTail = [];

// Wayland (Raspberry Pi OS Bookworm) or X11, whichever the desktop runs.
app.commandLine.appendSwitch("ozone-platform-hint", "auto");

function python() {
  const venv = path.join(HOME, ".venv", "bin", "python");
  return fs.existsSync(venv) ? venv : "python3";
}

function startBackend() {
  const args = ["-m", "nfc_login", "--web-ui", String(PORT)];
  if (fs.existsSync(CONFIG)) args.push("--config", CONFIG);
  if (SIMULATE) args.push("--simulate");
  logTail = [];
  backend = spawn(python(), args, { cwd: HOME, env: { ...process.env, PYTHONUNBUFFERED: "1" } });
  const keep = chunk => {
    process.stdout.write(chunk);
    logTail = logTail.concat(String(chunk).split("\n")).filter(Boolean).slice(-15);
  };
  backend.stdout.on("data", keep);
  backend.stderr.on("data", keep);
  backend.on("error", err => keep(`Couldn't start ${python()}: ${err.message}\n`));
  backend.on("exit", code => {
    backend = null;
    if (quitting) return;
    showLoading(`The kiosk service exited (code ${code}). Restarting in ${RESTART_SECONDS} s.`);
    setTimeout(() => { if (!quitting) { startBackend(); waitThenShow(); } }, RESTART_SECONDS * 1000);
  });
}

function answering() {
  return new Promise(resolve => {
    const req = http.get(URL + "api/kiosk", res => { res.resume(); resolve(res.statusCode === 200); });
    req.on("error", () => resolve(false));
    req.setTimeout(1000, () => { req.destroy(); resolve(false); });
  });
}

async function waitThenShow() {
  for (;;) {
    if (quitting || !backend) return;
    if (await answering()) break;
    await new Promise(r => setTimeout(r, 500));
  }
  if (win) win.loadURL(URL + (WINDOWED ? "" : "?kiosk=1"));
}

function showLoading(error) {
  if (!win) return;
  const query = error ? { error, log: logTail.join("\n") } : {};
  win.loadFile(path.join(__dirname, "loading.html"), { query });
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
  // The window only ever shows the local kiosk page or the loading page.
  win.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(URL) && !url.startsWith("file://")) event.preventDefault();
  });
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  showLoading();
}

app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((_wc, _perm, done) => done(false));
  globalShortcut.register("Control+Alt+Q", () => app.quit());
  createWindow();
  startBackend();
  waitThenShow();
});

app.on("before-quit", () => {
  quitting = true;
  if (backend) backend.kill("SIGINT")   // lets Python release the GPIO pins;
});
app.on("window-all-closed", () => app.quit());
