#!/usr/bin/env python3
"""
generate_moon.py — render the current moon phase as a 1000×1000 RGBA PNG.

Coordinate system:
  - Positions come from the JPL DE421 ephemeris via Skyfield.
  - The Moon's body-fixed (selenographic) frame is built from the IAU WGCCRE
    2015 rotation elements so libration is represented accurately.
  - For each on-disk output pixel we back-project onto the unit sphere,
    convert to selenographic lon/lat, look up the texture, and apply a
    Minnaert photometric model (k = 0.8, close to observed lunar values).

Environment variables (all optional):
  MOON_DATA    directory that holds de421.bsp and moon_texture.jpg
               default: /opt/moonserve
  MOON_OUTPUT  path written atomically on success
               default: /var/www/moonserve/moon.png
"""

import math
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from skyfield.api import Loader

DATA_DIR     = Path(os.getenv("MOON_DATA",   "/opt/moonserve"))
OUTPUT_PATH  = Path(os.getenv("MOON_OUTPUT", "/var/www/moonserve/moon.png"))
TEXTURE_PATH = DATA_DIR / "moon_texture.jpg"
SIZE         = 1000


# ── IAU 2015 Moon body-frame ──────────────────────────────────────────────────

def moon_rotation_matrix(tt: float) -> np.ndarray:
    """
    3×3 rotation matrix M such that  M @ v_icrf = v_selenographic.
    tt: Terrestrial Time as a Julian date (skyfield Time.tt).
    Source: IAU WGCCRE 2015 (Archinal et al. 2018, CeMDA 130, 22).
    """
    d = tt - 2451545.0      # TT days from J2000.0
    T = d / 36525.0         # Julian centuries

    def ra(deg: float) -> float:
        return math.radians(deg % 360)

    E1  = ra(125.045 -   0.0529921 * d)
    E2  = ra(250.089 -   0.1059842 * d)
    E3  = ra(260.008 +  13.0120009 * d)
    E4  = ra(176.625 +  13.3407154 * d)
    E5  = ra(357.529 +   0.9856003 * d)
    E6  = ra(311.589 +  26.4057084 * d)
    E7  = ra(134.963 +  13.0649930 * d)
    E8  = ra(276.617 +   0.3287146 * d)
    E9  = ra( 34.226 +   1.7484877 * d)
    E10 = ra( 15.134 -   0.1589763 * d)
    E11 = ra(119.743 +   0.0036096 * d)
    E12 = ra(239.961 +   0.1643573 * d)
    E13 = ra( 25.053 +  12.9590088 * d)

    alpha = (269.9949 + 0.0031 * T
             - 3.8787 * math.sin(E1)  - 0.1204 * math.sin(E2)
             + 0.0700 * math.sin(E3)  - 0.0172 * math.sin(E4)
             + 0.0072 * math.sin(E6)  - 0.0052 * math.sin(E10)
             + 0.0043 * math.sin(E13))

    delta = (66.5392 + 0.0130 * T
             + 1.5419 * math.cos(E1)  + 0.0239 * math.cos(E2)
             - 0.0278 * math.cos(E3)  + 0.0068 * math.cos(E4)
             - 0.0029 * math.cos(E6)  + 0.0009 * math.cos(E7)
             + 0.0008 * math.cos(E10) - 0.0009 * math.cos(E13))

    W = (38.3213 + 13.17635815 * d - 1.4e-12 * d * d
         + 3.5610 * math.sin(E1)  + 0.1208 * math.sin(E2)
         - 0.0642 * math.sin(E3)  + 0.0158 * math.sin(E4)
         + 0.0252 * math.sin(E5)  - 0.0066 * math.sin(E6)
         - 0.0047 * math.sin(E7)  - 0.0046 * math.sin(E8)
         + 0.0028 * math.sin(E9)  + 0.0052 * math.sin(E10)
         + 0.0040 * math.sin(E11) + 0.0019 * math.sin(E12)
         - 0.0044 * math.sin(E13))

    a  = math.radians(alpha)
    d0 = math.radians(delta)
    w  = math.radians(W % 360)

    def Rx(t: float) -> np.ndarray:
        c, s = math.cos(t), math.sin(t)
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=float)

    def Rz(t: float) -> np.ndarray:
        c, s = math.cos(t), math.sin(t)
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=float)

    # ICRF → selenographic: Rz(−W) · Rx(−(90°−δ₀)) · Rz(−(α₀+90°))
    return Rz(-w) @ Rx(-(math.pi / 2 - d0)) @ Rz(-(a + math.pi / 2))


# ── Astronomy ─────────────────────────────────────────────────────────────────

def get_geometry() -> tuple[float, float, float, float]:
    """
    Returns (sub_earth_lon, sub_earth_lat, sub_solar_lon, sub_solar_lat)
    in selenographic degrees.  The sub-Earth point encodes libration.
    """
    loader = Loader(str(DATA_DIR))
    ts     = loader.timescale()
    eph    = loader("de421.bsp")
    t      = ts.now()

    e_pos = eph["earth"].at(t).position.au
    m_pos = eph["moon"].at(t).position.au
    s_pos = eph["sun"].at(t).position.au

    def unit(v: np.ndarray) -> np.ndarray:
        return v / np.linalg.norm(v)

    M = moon_rotation_matrix(t.tt)

    def icrf_to_latlon(v_icrf: np.ndarray) -> tuple[float, float]:
        v   = M @ unit(v_icrf)
        lat = math.degrees(math.asin(float(np.clip(v[2], -1.0, 1.0))))
        lon = math.degrees(math.atan2(float(v[1]), float(v[0])))
        return lon, lat

    e_lon, e_lat = icrf_to_latlon(e_pos - m_pos)   # sub-Earth  (libration)
    s_lon, s_lat = icrf_to_latlon(s_pos - m_pos)   # sub-Solar  (terminator)
    return e_lon, e_lat, s_lon, s_lat


# ── Synthetic texture fallback ────────────────────────────────────────────────

def make_synthetic_texture(w: int = 2048, h: int = 1024) -> Image.Image:
    """Procedural greyscale lunar-ish texture used when no real texture exists."""
    from numpy.random import default_rng
    rng = default_rng(42)

    tex = np.full((h, w, 3), 145, dtype=np.int16)

    # Approximate nearside maria (selenographic lon, lat, semi-axes in degrees)
    maria = [
        (-58,  33, 18, 13),   # Mare Imbrium
        ( 17,  28, 13,  9),   # Mare Serenitatis
        ( 30,   8, 17, 11),   # Mare Tranquillitatis
        ( 59,  17,  8,  6),   # Mare Crisium
        ( 55,  -4, 11,  8),   # Mare Fecunditatis
        (-58,  18, 10,  8),   # Oceanus Procellarum (part)
        (-15, -21, 10,  7),   # Mare Nubium
    ]
    xs = np.linspace(-180, 180, w)
    ys = np.linspace(90, -90, h)
    xg, yg = np.meshgrid(xs, ys)

    for (cx, cy, rx, ry) in maria:
        mask = ((xg - cx) ** 2 / rx ** 2 + (yg - cy) ** 2 / ry ** 2) < 1
        tex[mask] -= 55

    noise = rng.integers(-18, 18, (h, w, 1), dtype=np.int16)
    tex = np.clip(tex + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(tex, "RGB")


# ── Rendering ─────────────────────────────────────────────────────────────────

def render_moon(
    sub_e_lon: float, sub_e_lat: float,
    sub_s_lon: float, sub_s_lat: float,
    texture: Image.Image,
    size: int = 1000,
) -> Image.Image:
    """
    Project the texture onto the visible lunar hemisphere and shade it.

    View coordinate frame (orthonormal, all in selenographic 3-D):
      z_ax  — toward Earth  (= sub-Earth point on unit sphere)
      y_ax  — toward lunar north, projected onto the disk plane
      x_ax  — toward east   (= y_ax × z_ax)

    Photometric model: Minnaert with k = 0.8.
      brightness = cos_i^k · cos_e^(k−1),  clipped to [0, 1]
    where cos_i = angle to sun, cos_e = angle to observer (= pz in view coords).
    cos_e is clamped to ≥ 0.05 to prevent near-limb divergence.
    """
    tex = np.array(texture.convert("RGB"), dtype=np.float32)
    th, tw = tex.shape[:2]

    def sph(lon_d: float, lat_d: float) -> np.ndarray:
        lo, la = math.radians(lon_d), math.radians(lat_d)
        return np.array([math.cos(la) * math.cos(lo),
                         math.cos(la) * math.sin(lo),
                         math.sin(la)], dtype=float)

    z_ax  = sph(sub_e_lon, sub_e_lat)
    north = np.array([0.0, 0.0, 1.0])
    y_ax  = north - float(north @ z_ax) * z_ax
    yn    = np.linalg.norm(y_ax)
    y_ax  = y_ax / yn if yn > 1e-6 else np.array([0.0, 1.0, 0.0])
    x_ax  = np.cross(y_ax, z_ax)
    x_ax /= np.linalg.norm(x_ax)

    sun_dir = sph(sub_s_lon, sub_s_lat)

    # Pixel grid: x+ = east (right), y+ = north (up)
    r    = size / 2.0 - 1.0
    half = size / 2.0
    iy, ix = np.mgrid[0:size, 0:size]
    px   = (ix - half + 0.5) / r
    py   = (half - iy - 0.5) / r

    r2      = px ** 2 + py ** 2
    r_val   = np.sqrt(r2)
    on_disk = r2 < 1.0
    pz      = np.sqrt(np.clip(1.0 - r2, 0.0, 1.0))

    # Each on-disk pixel as a selenographic 3-D unit vector
    Px = px * x_ax[0] + py * y_ax[0] + pz * z_ax[0]
    Py = px * x_ax[1] + py * y_ax[1] + pz * z_ax[1]
    Pz = px * x_ax[2] + py * y_ax[2] + pz * z_ax[2]

    # Selenographic lon/lat → texture coordinates (equirectangular)
    lon_d = np.degrees(np.arctan2(Py, Px))
    lat_d = np.degrees(np.arcsin(np.clip(Pz, -1.0, 1.0)))
    u = ((lon_d + 180.0) / 360.0 * tw).astype(np.int32) % tw
    v = ((90.0 - lat_d) / 180.0 * th).astype(np.int32).clip(0, th - 1)
    color = tex[v, u]   # shape (size, size, 3)

    # Minnaert k = 0.8
    k   = 0.8
    ci  = np.clip(Px * sun_dir[0] + Py * sun_dir[1] + Pz * sun_dir[2], 0.0, 1.0)
    ce  = np.clip(pz, 0.05, 1.0)   # emission cosine; clamp prevents limb divergence
    bright = np.where(
        on_disk & (ci > 0.0),
        np.clip(np.power(ci, k) * np.power(ce, k - 1.0), 0.0, 1.0),
        0.0,
    )

    out_rgb = (color * bright[..., None]).clip(0, 255).astype(np.uint8)
    alpha   = (np.clip(0.5 + (1.0 - r_val) * r, 0.0, 1.0) * 255).astype(np.uint8)
    return Image.fromarray(np.dstack([out_rgb, alpha]), "RGBA")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    if TEXTURE_PATH.exists():
        print(f"Loading texture: {TEXTURE_PATH}")
        texture = Image.open(TEXTURE_PATH)
    else:
        print("Texture not found — using built-in synthetic fallback.")
        print(f"For a photorealistic image install a NASA equirectangular texture at:")
        print(f"  {TEXTURE_PATH}")
        texture = make_synthetic_texture()

    print("Computing moon geometry …")
    e_lon, e_lat, s_lon, s_lat = get_geometry()
    print(f"  sub-Earth  ({e_lon:+.2f}°, {e_lat:+.2f}°)  "
          f"sub-Solar ({s_lon:+.2f}°, {s_lat:+.2f}°)")

    print("Rendering …")
    img = render_moon(e_lon, e_lat, s_lon, s_lat, texture, SIZE)

    # Atomic write: never let nginx read a partial PNG
    tmp = OUTPUT_PATH.with_suffix(".tmp.png")
    img.save(str(tmp), "PNG", compress_level=6)
    tmp.rename(OUTPUT_PATH)
    print(f"Saved → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
