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
DEPS_STAMP="$INSTALL_DIR/.deps_installed"
if [[ ! -f "$DEPS_STAMP" ]] || [[ "$SCRIPT_DIR/requirements.txt" -nt "$DEPS_STAMP" ]]; then
    "$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
    "$INSTALL_DIR/venv/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
    touch "$DEPS_STAMP"
    echo "Python dependencies installed."
else
    echo "Python dependencies up to date."
fi

# ── Copy generator script ─────────────────────────────────────────────────────
cp "$SCRIPT_DIR/generate_moon.py" "$INSTALL_DIR/"

# ── Download JPL ephemeris (≈17 MB, needed once) ─────────────────────────────
if [[ ! -f "$INSTALL_DIR/de421.bsp" ]]; then
    echo "Downloading DE421 ephemeris (~17 MB) …"
    "$INSTALL_DIR/venv/bin/python" - <<PY
from skyfield.api import Loader
load = Loader("$INSTALL_DIR")
load("de421.bsp")
print("  de421.bsp ready.")
PY
fi

# ── Lunar texture ─────────────────────────────────────────────────────────────
TEXTURE_URL="https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/lroc_color_2k.jpg"
if [[ ! -f "$INSTALL_DIR/moon_texture.jpg" ]]; then
    echo "Downloading NASA lunar texture (~447 KB) …"
    if wget -q -O "$INSTALL_DIR/moon_texture.jpg" "$TEXTURE_URL"; then
        echo "  Texture ready."
    else
        rm -f "$INSTALL_DIR/moon_texture.jpg"
        echo "  Download failed — will use built-in synthetic texture instead."
        echo "  To install manually later:"
        echo "    wget -O $INSTALL_DIR/moon_texture.jpg $TEXTURE_URL"
    fi
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
systemctl start moonserve.service \
    && echo "  Image written to $WWW_DIR/moon.png" \
    || { echo "  Generation failed — check: journalctl -u moonserve.service"; exit 1; }

# ── nginx ─────────────────────────────────────────────────────────────────────
NGINX_OK=false
if command -v nginx &>/dev/null; then
    mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled
    CONF=/etc/nginx/sites-available/moonserve
    sed "s/__PORT__/$PORT/g" "$SCRIPT_DIR/nginx-moonserve.conf" > "$CONF"
    ln -sf "$CONF" /etc/nginx/sites-enabled/moonserve

    # The default site uses default_server on port 80; only a conflict if PORT=80.
    if [[ "$PORT" == "80" ]] && [[ -L /etc/nginx/sites-enabled/default ]]; then
        echo "Note: default nginx site also claims port 80 — removing it to avoid conflict."
        rm /etc/nginx/sites-enabled/default
    fi

    systemctl enable nginx
    if nginx -t 2>&1; then
        systemctl reload-or-restart nginx
        echo "nginx configured."
        NGINX_OK=true
    else
        echo "WARNING: nginx config test failed — fix the conflict and run:"
        echo "  sudo nginx -t && sudo systemctl reload nginx"
        echo "(moonserve timer and image generation are running normally)"
    fi
else
    echo "nginx not found. Install it: sudo apt install nginx"
    echo "Then re-run this script."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo ""
if $NGINX_OK; then
    echo "=== Setup complete ==="
    echo "  Image endpoint: http://${IP}:${PORT}/moon.png"
    echo "  Viewer:         http://${IP}:${PORT}/"
else
    echo "=== Setup complete (nginx not yet configured) ==="
    echo "  Install/fix nginx, then re-run this script to finish."
    echo "  Once ready, endpoint will be: http://${IP}:${PORT}/moon.png"
fi
echo "  Logs:           journalctl -fu moonserve.service"
echo "  Force refresh:  sudo systemctl start moonserve.service"
