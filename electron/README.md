# NFC Kiosk desktop app (Electron)

An app the Pi can just run: it boots the whole sign-in system and shows it
fullscreen on the 800x480 touchscreen. On launch it works through these steps,
ticking each one off on screen:

1. **Database:** checks MariaDB answers, and starts it (`sudo systemctl start
   mariadb`) if it's on this Pi and down.
2. **Kiosk program:** starts `python -m nfc_login --web-ui 8081` from the
   rpi-login-system folder, which brings up
3. the **card reader**, 4. **keypad**, 5. **buzzer** and 6. the **live web
   page** on port 8080,
7. then shows the **kiosk screen**.

If a step fails, the screen says which one and shows the last log lines, then
tries again every 5 seconds, so a Pi that boots before its database or a
reader that's plugged in late recovers by itself. Quitting (**Ctrl+Alt+Q**)
stops the kiosk program (it releases the GPIO pins) and stops MariaDB too if
the app was the one that started it.

```
NFC Kiosk app ──starts──▶ MariaDB (if needed)
     │        ──starts──▶ python -m nfc_login --web-ui 8081
     │                      (reader, keypad, buzzer, :8080 live page)
     └──shows──▶ http://127.0.0.1:8081/   (nfc_login/ui/kiosk.html)
```

The kiosk page listens on 127.0.0.1 only, so nothing on the network can press
keys on it.

## Install on the Pi (Raspberry Pi OS 64-bit)

1. Set up the kiosk as usual: `bash scripts/install.sh` (database, venv,
   `config.toml`).
2. Get `nfc-kiosk-0.2.0-arm64.deb`: build it (below) or copy a ready one.
3. `bash electron/install-pi.sh path/to/nfc-kiosk-0.2.0-arm64.deb`

That installs the app, turns off the Tkinter kiosk service (two programs
can't share the card reader) and adds an autostart entry, so the kiosk opens
fullscreen when the Pi logs in to the desktop. **Ctrl+Alt+Q** quits.

There's also an AppImage (`nfc-kiosk-0.2.0-arm64.AppImage`) that runs without
installing: `chmod +x` it and run it. It needs `sudo apt install libfuse2`
(`libfuse2t64` on newer releases).

## Options

| Option | Environment variable | Default |
|---|---|---|
| `--home PATH` | `NFC_KIOSK_HOME` | `~/rpi-login-system` |
| `--config PATH` | `NFC_KIOSK_CONFIG` | `<home>/config.toml` |
| `--port N` | `NFC_KIOSK_PORT` | `8081` |
| `--simulate` | | no reader or keypad: a "tap card" box appears |
| `--windowed` | | an 800x480 window instead of fullscreen kiosk mode |

It uses `<home>/.venv/bin/python` when it exists, otherwise `python3`.

## Build

Needs Node.js 20 or newer. On any Linux PC or on the Pi:

```bash
cd electron
npm install
npm run dist:pi        # dist/nfc-kiosk-0.2.0-arm64.deb and .AppImage
npm run simulate       # try it on a PC (needs the database; see docs/setup.md)
```

Without the app, the same screen runs in any browser on the Pi:
`python3 -m nfc_login --web-ui` then open http://127.0.0.1:8081/.
