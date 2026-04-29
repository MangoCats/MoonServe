# MoonServe

An HTTP image server that renders an accurate, photorealistic 1000×1000 PNG of the current moon phase. Designed to run continuously on a Raspberry Pi with a minimal memory and CPU footprint.

## How it works

A Python script generates the image and nginx serves it as a static file. The two concerns are fully decoupled: a generation failure never interrupts serving, and nginx never touches running Python code.

**Generation** runs as a systemd oneshot service on a 30-minute timer. Each run:

1. Loads current Earth/Moon/Sun positions from the JPL DE421 ephemeris (Skyfield).
2. Builds the Moon's selenographic (body-fixed) coordinate frame using the IAU WGCCRE 2015 rotation model with 13 nutation terms — this correctly captures **libration** (the ~7° apparent wobble that lets us see slightly different parts of the lunar surface over time).
3. Computes the **sub-Earth point** (center of the visible disk) and **sub-Solar point** (center of the illuminated hemisphere).
4. Back-projects every pixel in the output grid onto the unit sphere, converts to selenographic lon/lat, and looks up the color in an equirectangular lunar texture.
5. Shades each pixel using a **Minnaert photometric model** (k = 0.8), which approximates the Moon's observed near-uniform brightness at full phase and smooth terminator gradient at quarter phase.
6. Writes the result atomically (write to `.tmp.png`, then `rename`) so nginx never reads a partial file.

**Serving** is handled entirely by nginx. The generated PNG is a static file on disk with a 30-minute `Cache-Control` header matching the generation interval.

## Requirements

- Raspberry Pi running Raspberry Pi OS, Debian, or Ubuntu
- Python 3.10+
- nginx
- Internet access on first run only (to download the DE421 ephemeris, ~17 MB)

## Installation

```bash
sudo apt update && sudo apt install -y python3 python3-venv nginx
sudo ./setup.sh
```

`setup.sh` performs every step automatically:

- Creates a dedicated `moonserve` system user
- Creates a Python virtualenv at `/opt/moonserve/venv` and installs dependencies
- Downloads `de421.bsp` from NASA JPL (~17 MB, needed once)
- Installs and enables the systemd service and timer
- Generates the first image immediately
- Installs and activates the nginx site configuration

### Port

The default port is **1969**. This is defined once, at the top of `setup.sh`:

```bash
PORT="${PORT:-1969}"
```

To install on a different port without editing any file:

```bash
PORT=8080 sudo ./setup.sh
```

### Lunar texture

The server works immediately using a built-in synthetic texture (procedural grey sphere with approximate maria). For a photorealistic result, install a real NASA lunar texture:

1. Visit the **NASA SVS CGI Moon Kit** (public domain): https://svs.gsfc.nasa.gov/4720
2. Download the color map at any resolution ≥ 2k (equirectangular projection, JPG or PNG).
3. Save it as `/opt/moonserve/moon_texture.jpg`.
4. Force a regeneration: `sudo systemctl start moonserve.service`

The script detects the texture automatically on the next run.

## Endpoints

| URL | Description |
|-----|-------------|
| `http://<pi-ip>:1969/moon.png` | Current moon phase image (1000×1000 RGBA PNG) |
| `http://<pi-ip>:1969/` | Minimal browser viewer (black background, centered image) |

## Operations

```bash
# View live generation logs
journalctl -fu moonserve.service

# Force an immediate regeneration
sudo systemctl start moonserve.service

# Check timer status and next scheduled run
systemctl status moonserve.timer

# Restart nginx
sudo systemctl reload nginx
```

## Configuration

### Changing the port after install

Edit `/opt/moonserve/venv` — no, just re-run setup with the new port:

```bash
PORT=8080 sudo ./setup.sh
```

### Generator environment variables

Both variables are set in `moonserve.service` and can be overridden there:

| Variable | Default | Description |
|----------|---------|-------------|
| `MOON_DATA` | `/opt/moonserve` | Directory containing `de421.bsp` and `moon_texture.jpg` |
| `MOON_OUTPUT` | `/var/www/moonserve/moon.png` | Path where the PNG is written |

After editing the service file, reload systemd:

```bash
sudo systemctl daemon-reload
```

### Regeneration interval

The timer is configured in `moonserve.timer`. To change from 30 minutes to hourly, edit the installed unit:

```bash
sudo systemctl edit --full moonserve.timer
# Change OnUnitActiveSec=30min to OnUnitActiveSec=1h
sudo systemctl daemon-reload
```

## File structure

### Repository

```
MoonServe/
├── generate_moon.py       # Image generation script
├── requirements.txt       # Python dependencies (skyfield, Pillow, numpy)
├── moonserve.service      # systemd oneshot service unit
├── moonserve.timer        # systemd timer unit (every 30 min)
├── nginx-moonserve.conf   # nginx site config template (__PORT__ placeholder)
└── setup.sh               # One-shot installer
```

### Installed layout

```
/opt/moonserve/
├── generate_moon.py       # Deployed copy of the generator
├── de421.bsp              # JPL planetary ephemeris (~17 MB, downloaded by setup.sh)
├── moon_texture.jpg       # NASA lunar texture (user-supplied, optional)
└── venv/                  # Python virtualenv

/var/www/moonserve/
└── moon.png               # Generated image (rewritten every 30 min)

/etc/systemd/system/
├── moonserve.service
└── moonserve.timer

/etc/nginx/sites-available/
└── moonserve              # Rendered nginx config (PORT substituted by setup.sh)
```

## Accuracy notes

- **Phase and terminator**: accurate to the DE421 ephemeris, which has sub-kilometre positional error for the Moon.
- **Libration**: computed from the full IAU 2015 rotation model (13 nutation terms); typical error < 0.1°.
- **Photometry**: Minnaert model (k = 0.8) matches observed lunar disk brightness distribution but does not model topographic shadows near the terminator. A displacement/height map would be required for that level of detail.
- **Image orientation**: lunar north is up, east is right (standard astronomical convention for the nearside).
