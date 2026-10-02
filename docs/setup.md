# Setup on the Raspberry Pi

## 1. Operating system

Flash **Raspberry Pi OS (64-bit) with desktop** (Bookworm) with Raspberry Pi
Imager. Set a hostname, user and Wi-Fi in the imager's settings, and enable
SSH so you can run admin commands from another computer.

The Pi has no battery-backed clock. Sign-in times come from the system
clock, so make sure the Pi has network access for time sync (or add an RTC).
Check with `timedatectl`.

## 2. One-command setup

```bash
git clone https://github.com/LunarcatOwO/rpi-login-system.git ~/rpi-login-system && bash ~/rpi-login-system/scripts/setup-pi.sh
```

(The repo is private: when git asks for a password, use a GitHub personal
access token.) `setup-pi.sh` runs `scripts/install.sh` (below), asks for the
admin PIN, downloads the Electron runtime and makes the desktop app
(`electron/`) open fullscreen when the Pi boots, sets the Pi to log in to the
desktop by itself, turns off screen blanking and offers to reboot. Running it
again is safe.

### What install.sh does

To set up only the Python kiosk (no desktop app), run `bash scripts/install.sh`.
The script:

1. installs `python3-venv`, `python3-tk` and `mariadb-server`,
2. enables SPI,
3. makes a virtual environment in `.venv` with `requirements-pi.txt`,
4. creates a `nfc_login` MariaDB database and user with a random password,
   and writes it into `config.toml`,
5. creates the tables and the first season (named after the current year),
6. installs and enables the `nfc-login` systemd service,
7. adds a 03:00 cron job that closes forgotten sign-outs.

Then:

```bash
.venv/bin/python -m nfc_login.admin set-admin-pin
.venv/bin/python -m nfc_login.admin user add "First Person" --section A
sudo reboot
```

After the reboot the kiosk is on screen and the live page is at
`http://<pi-address>:8080/` (port set in `[web]`; `enabled = false` turns it
off). Give the Pi a fixed address on your router so the link doesn't change.

## 3. Manual install (what the script does)

```bash
sudo apt install -y python3-venv python3-tk mariadb-server
sudo raspi-config nonint do_spi 0

python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements-pi.txt
```

Create the database:

```sql
-- sudo mariadb
CREATE DATABASE nfc_login CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'nfc_login'@'localhost' IDENTIFIED BY 'pick-a-password';
GRANT ALL PRIVILEGES ON nfc_login.* TO 'nfc_login'@'localhost';
```

Configure and initialise:

```bash
cp config.example.toml config.toml
nano config.toml                 # set [database] password
chmod 600 config.toml
.venv/bin/python -m nfc_login.admin init-db
```

Run it by hand to check the hardware:

```bash
.venv/bin/python -m nfc_login --windowed
```

## 4. Start on boot

`scripts/nfc-login.service` runs the kiosk on the desktop session:

```bash
sudo cp scripts/nfc-login.service /etc/systemd/system/   # edit User/paths first
sudo systemctl daemon-reload
sudo systemctl enable --now nfc-login
journalctl -u nfc-login -f       # logs
```

If the window doesn't appear (some Wayland setups), use desktop autostart
instead: create `~/.config/autostart/nfc-login.desktop` with

```ini
[Desktop Entry]
Type=Application
Name=NFC Login
Path=/home/pi/rpi-login-system
Exec=/home/pi/rpi-login-system/.venv/bin/python -m nfc_login --config /home/pi/rpi-login-system/config.toml
```

and disable the service. The kiosk hides the mouse pointer in fullscreen. To
stop the screen going blank, turn off Screen Blanking in `raspi-config` →
Display Options.

Stop the kiosk before running admin commands that use the reader
(`tag enroll` without `--uid`, `tag read`):

```bash
sudo systemctl stop nfc-login
```

## 5. Backups

Everything lives in MariaDB. A nightly dump to a USB stick or another machine
is enough:

```bash
sudo mariadb-dump nfc_login > nfc_login-$(date +%F).sql
```

Restore with `sudo mariadb nfc_login < nfc_login-YYYY-MM-DD.sql`.

## 6. Updating

```bash
cd ~/rpi-login-system
git pull
.venv/bin/pip install -r requirements-pi.txt
.venv/bin/python -m nfc_login.admin init-db     # applies any new tables; safe to re-run
sudo systemctl restart nfc-login
```

## Troubleshooting

| Problem | Check |
|---|---|
| `No module named 'board'` | You're not in the venv, or `requirements-pi.txt` isn't installed |
| PN532 not found / `RuntimeError` at start | SPI turned on (`ls /dev/spidev0.0`)? DIP switches 1 OFF 2 ON? SS on pin 29? |
| Cards sometimes need a second tap to update | Hold them still a moment longer; if it keeps happening, lower `spi_baudrate` or shorten the wires |
| Keys come out wrong | Swap entries in `[hardware.keypad] rows`/`cols` |
| Keys repeat or ghost | Add the 10 kΩ pull-downs on the column lines |
| `Access denied for user` | Password in `config.toml` matches the MariaDB user? |
| `No active season` | `python -m nfc_login.admin season new` |
| Live page doesn't load | Same network as the Pi? `journalctl -u nfc-login` shows "live page on http://..."; another program using port 8080? |
| Times are wrong | `timedatectl`; the Pi needs network time |
