# -*- coding: utf-8 -*-
"""
icon_generator.py - Dibuja el logo oficial app_icon.ico (Rojo y Negro) con Pillow.
Analizer IA local  •  by @tomasanfo

Uso:  python icon_generator.py      (genera/regenera app_icon.ico junto a este archivo)
      from icon_generator import ensure_icon   (la crea solo si no existe)
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(BASE_DIR, "app_icon.ico")

BG = "#070709"
BORDER = "#DC2626"
NEON = "#FF2D2D"
SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32)]


def _star_points(cx, cy, outer, inner):
    pts = []
    for i in range(10):
        radius = outer if i % 2 == 0 else inner
        ang = math.radians(-90 + i * 36)
        pts.append((cx + radius * math.cos(ang), cy + radius * math.sin(ang)))
    return pts


def _bars(size):
    """Rectángulos (x0, y0, x1, y1) de las barras de onda: 3 a cada lado de la estrella."""
    cx = cy = size / 2.0
    star_half = size * 0.15
    gap = size * 0.028
    bar_w = size * 0.05
    heights = (0.52, 0.38, 0.24)           # de adentro hacia afuera
    rects = []
    for side in (-1, 1):
        for i, hf in enumerate(heights):
            offset = star_half + gap + i * (bar_w + gap)
            x0 = cx + offset if side > 0 else cx - offset - bar_w
            h = size * hf
            rects.append((x0, cy - h / 2, x0 + bar_w, cy + h / 2))
    return rects


def draw_logo(size=1024):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    margin = int(size * 0.03)
    border = max(2, int(size * 0.035))
    d.rounded_rectangle([margin, margin, size - margin, size - margin],
                        radius=int(size * 0.22), fill=BG, outline=BORDER, width=border)

    cx = cy = size / 2.0
    star = _star_points(cx, cy, size * 0.17, size * 0.07)
    bars = _bars(size)

    # Resplandor neón: mismas formas, desenfocadas
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for r in bars:
        gd.rounded_rectangle(r, radius=(r[2] - r[0]) / 2, fill=NEON)
    gd.polygon(star, fill=NEON)
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.022))
    img = Image.alpha_composite(img, glow)
    img = Image.alpha_composite(img, glow)

    # Formas nítidas encima
    d = ImageDraw.Draw(img)
    for r in bars:
        d.rounded_rectangle(r, radius=(r[2] - r[0]) / 2, fill=NEON)
    d.polygon(star, fill="#FF5A5A")
    return img


def ensure_icon(path=None, force=False):
    """Devuelve la ruta del .ico; lo genera si no existe (o si force=True)."""
    path = path or ICON_PATH
    if os.path.isfile(path) and not force:
        return path
    logo = draw_logo(1024).resize((256, 256), Image.LANCZOS)
    logo.save(path, format="ICO", sizes=SIZES)
    return path


if __name__ == "__main__":
    out = ensure_icon(force=True)
    print(f"Ícono generado: {out}")
