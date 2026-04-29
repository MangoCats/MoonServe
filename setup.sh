#!/usr/bin/env bash
# setup.sh — install MoonServe on a Raspberry Pi (Debian/Ubuntu/Raspberry Pi OS)
# Run as root: sudo ./setup.sh
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run as root: sudo $0"; exit 1; }

PORT="${PORT:-1969}"          # override: PORT=8080 sudo ./setup.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR=/opt/moonserve
WWW_DIR=/var/www/moonserve

echo "=== MoonServe setup ==="

# ── Service user ──────────────────────────────────────────────────────────────
if ! id -u moonserve &>/dev/null; then
    useradd -r -s /usr/sbin/nologin -d "$INSTALL_DIR" moonserve
    echo "Created system user: moonserve"
fi

# ── Directories ───────────────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR" "$WWW_DIR"

# ── Python virtualenv + deps ──────────────────────────────────────────────────
if [[ ! -x "$INSTALL_DIR/venv/bin/python" ]]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
echo "Python dependencies installed."

# ── Copy generator script ─────────────────────────────────────────────────────
cp "$SCRIPT_DIR/generate_moon.py" "$INSTALL_DIR/"

# ── Download JPL ephemeris (≈17 MB, needed once) ─────────────────────────────
if [[ ! -f "$INSTALL_DIR/de421.bsp" ]]; then
    echo "Downloading DE421 ephemeris (~17 MB) …"
    "$INSTALL_DIR/venv/bin/python" - <<'PY'
from skyfield.api import Loader
import os
load = Loader(os.environ["INSTALL_DIR"])
load("de421.bsp")
print("  de421.bsp ready.")
PY
fi

# ── Lunar texture (optional but recommended) ──────────────────────────────────
if [[ ! -f "$INSTALL_DIR/moon_texture.jpg" ]]; then
    echo ""
    echo "┌─────────────────────────────────────────────────────────────────────┐"
    echo "│  OPTIONAL: install a NASA lunar texture for photorealistic output.  │"
    echo "│                                                                     │"
    echo "│  Recommended source — NASA SVS CGI Moon Kit (public domain):        │"
    echo "│    https://svs.gsfc.nasa.gov/4720                                   │"
    echo "│                                                                     │"
    echo "│  Download the color map (any resolution ≥ 2k) and save it as:      │"
    echo "│    /opt/moonserve/moon_texture.jpg                                  │"
    echo "│                                                                     │"
    echo "│  Without it the server uses a built-in synthetic fallback.         │"
    echo "└─────────────────────────────────────────────────────────────────────┘"
    echo ""
fi

# ── Permissions ───────────────────────────────────────────────────────────────
chown -R moonserve:moonserve "$INSTALL_DIR"
chmod 755 "$INSTALL_DIR"
chown -R moonserve:moonserve "$WWW_DIR"
chmod 755 "$WWW_DIR"

# ── Systemd ───────────────────────────────────────────────────────────────────
cp "$SCRIPT_DIR/moonserve.service" /etc/systemd/system/
cp "$SCRIPT_DIR/moonserve.timer"   /etc/systemd/system/
systemctl daemon-reload
systemctl enable moonserve.timer
systemctl start moonserve.timer
echo "systemd timer enabled (runs every 30 min, persists across reboots)."

echo "Generating first image …"
systemctl start moonserve.service
systemctl is-active --quiet moonserve.service \
    && echo "  Image written to $WWW_DIR/moon.png" \
    || { echo "  Generation failed — check: journalctl -u moonserve.service"; exit 1; }

# ── nginx ─────────────────────────────────────────────────────────────────────
if command -v nginx &>/dev/null; then
    CONF=/etc/nginx/sites-available/moonserve
    sed "s/__PORT__/$PORT/g" "$SCRIPT_DIR/nginx-moonserve.conf" > "$CONF"
    ln -sf "$CONF" /etc/nginx/sites-enabled/moonserve
    if [[ -L /etc/nginx/sites-enabled/default ]]; then
        echo "Note: default nginx site is active — remove it if it conflicts on port $PORT:"
        echo "      rm /etc/nginx/sites-enabled/default"
    fi
    nginx -t && systemctl reload nginx
    echo "nginx configured."
else
    echo "nginx not found. Install it: sudo apt install nginx"
    echo "Then copy nginx-moonserve.conf to /etc/nginx/sites-available/ and reload."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo ""
echo "=== Setup complete ==="
echo "  Image endpoint: http://${IP}:${PORT}/moon.png"
echo "  Viewer:         http://${IP}:${PORT}/"
echo "  Logs:           journalctl -fu moonserve.service"
echo "  Force refresh:  sudo systemctl start moonserve.service"
