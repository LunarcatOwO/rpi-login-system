# NFC Kiosk desktop app (Electron)

An app the Pi can just run: it starts the Python kiosk and shows its screen
fullscreen on the 800x480 touchscreen. Everything the kiosk does (cards,
keypad, buzzer, admin menu, live web page on :8080) stays in Python; the app
replaces only the Tkinter window.

```
NFC Kiosk app ──starts──▶ python -m nfc_login --web-ui 8081
     │                      (reader, keypad, buzzer, database)
     └──shows──▶ http://127.0.0.1:8081/   (nfc_login/ui/kiosk.html)
```

The kiosk page listens on 127.0.0.1 only, so nothing on the network can press
keys on it. If the Python side stops, the app shows the last log lines and
starts it again after 5 seconds.

## Install on the Pi (Raspberry Pi OS 64-bit)

1. Set up the kiosk as usual: `bash scripts/install.sh` (database, venv,
   `config.toml`).
2. Get `nfc-kiosk-0.1.0-arm64.deb`: build it (below) or copy a ready one.
3. `bash electron/install-pi.sh path/to/nfc-kiosk-0.1.0-arm64.deb`

That installs the app, turns off the Tkinter kiosk service (two programs
can't share the card reader) and adds an autostart entry, so the kiosk opens
fullscreen when the Pi logs in to the desktop. **Ctrl+Alt+Q** quits.

There's also an AppImage (`nfc-kiosk-0.1.0-arm64.AppImage`) that runs without
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
npm run dist:pi        # dist/nfc-kiosk-0.1.0-arm64.deb and .AppImage
npm run simulate       # try it on a PC (needs the database; see docs/setup.md)
```

Without the app, the same screen runs in any browser on the Pi:
`python3 -m nfc_login --web-ui` then open http://127.0.0.1:8081/.
