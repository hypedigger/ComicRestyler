# -*- coding: utf-8 -*-
"""Génère app.ico : palette de peintre sur fond dégradé violet, multi-tailles."""
import math
import os

from PIL import Image, ImageDraw

SIZE = 256
APP_DIR = os.path.dirname(os.path.abspath(__file__))


def rounded_gradient(size, c1, c2, radius_ratio=0.22):
    """Carré arrondi rempli d'un dégradé diagonal c1 -> c2."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGBA", (size, size))
    px = grad.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2.0 * size - 2)
            px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)) + (255,)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * radius_ratio), fill=255)
    im.paste(grad, (0, 0), mask)
    return im


def draw_palette(im):
    """Palette de peintre inclinée avec touches de couleur et pinceau."""
    d = ImageDraw.Draw(im)
    s = im.size[0]

    # palette : grande ellipse crème avec encoche de pouce
    d.ellipse([s * 0.14, s * 0.22, s * 0.86, s * 0.82],
              fill=(246, 240, 228, 255), outline=(60, 45, 70, 255),
              width=max(2, s // 48))
    d.ellipse([s * 0.60, s * 0.55, s * 0.78, s * 0.72],
              fill=(0, 0, 0, 0), outline=(60, 45, 70, 255),
              width=max(2, s // 48))
    # trou du pouce transparent : re-perce le dégradé
    hole = Image.new("L", im.size, 0)
    ImageDraw.Draw(hole).ellipse(
        [s * 0.615, s * 0.565, s * 0.765, s * 0.705], fill=255)

    # touches de peinture
    colors = [(88, 101, 242), (35, 165, 90), (240, 178, 50),
              (242, 63, 67), (0, 168, 252)]
    cx, cy, r = s * 0.44, s * 0.5, s * 0.24
    for i, c in enumerate(colors):
        ang = math.radians(140 + i * 42)
        x = cx + r * math.cos(ang)
        y = cy + r * math.sin(ang) * 0.8
        rr = s * 0.055
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=c + (255,))

    # pinceau en diagonale
    w = max(3, s // 26)
    d.line([s * 0.30, s * 0.88, s * 0.72, s * 0.30],
           fill=(60, 45, 70, 255), width=w)
    d.polygon([(s * 0.72, s * 0.30), (s * 0.80, s * 0.18),
               (s * 0.78, s * 0.34)], fill=(230, 120, 160, 255))
    return im


def main():
    base = rounded_gradient(SIZE, (84, 66, 160), (150, 80, 190))
    icon = draw_palette(base)
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    out = os.path.join(APP_DIR, "app.ico")
    icon.save(out, format="ICO", sizes=sizes)
    icon.save(os.path.join(APP_DIR, "app_icon_preview.png"))
    print("OK ->", out)


if __name__ == "__main__":
    main()
