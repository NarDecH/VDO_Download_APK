"""Generate the VDO Grabber brand assets: app icon (.ico, .png) + banner.

Drawn with Pillow at 4x supersampling for smooth curves:
  - rounded-square gradient (coral -> amber, matching the in-app logo)
  - white download tray + arrow, with a small play-triangle accent
Outputs: assets/logo-512.png, assets/icon.ico, docs/assets/logo.png
"""

from __future__ import annotations

import os
from PIL import Image, ImageDraw

S = 2048          # supersample canvas
OUT_512 = 512


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assets = os.path.join(root, "assets")
    os.makedirs(assets, exist_ok=True)

    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # ---- gradient rounded square ------------------------------------------
    c1, c2 = (255, 95, 109), (255, 195, 113)          # #ff5f6d -> #ffc371
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        gd.line([(0, y), (S, y)], fill=lerp(c1, c2, y / S))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)

    # ---- download tray ------------------------------------------------------
    w = int(S * 0.035)                                # stroke width
    tray_l, tray_r, tray_y = int(S * 0.26), int(S * 0.74), int(S * 0.66)
    d.line([(tray_l, tray_y), (tray_l, tray_y + int(S * 0.10))], fill="white", width=w)
    d.line([(tray_r, tray_y), (tray_r, tray_y + int(S * 0.10))], fill="white", width=w)
    d.line([(tray_l - int(w * 0.5), tray_y + int(S * 0.10)), (tray_r + int(w * 0.5), tray_y + int(S * 0.10))],
           fill="white", width=w)
    for x, y in [(tray_l, tray_y + int(S * 0.10)), (tray_r, tray_y + int(S * 0.10))]:
        d.line([(x - w, y), (x + w, y)], fill="white", width=w)

    # ---- arrow into the tray ------------------------------------------------
    ax = S // 2
    d.line([(ax, int(S * 0.18)), (ax, int(S * 0.50))], fill="white", width=w)
    ah = int(S * 0.09)
    d.polygon([(ax - ah, int(S * 0.42)), (ax + ah, int(S * 0.42)), (ax, int(S * 0.54))], fill="white")

    # ---- play-triangle accent ------------------------------------------------
    px, py, ph = int(S * 0.70), int(S * 0.24), int(S * 0.075)
    d.polygon([(px - ph * 0.9, py - ph), (px - ph * 0.9, py + ph), (px + ph * 1.1, py)], fill="white")

    # ---- outputs -------------------------------------------------------------
    big = img.resize((OUT_512, OUT_512), Image.LANCZOS)
    big.save(os.path.join(assets, "logo-512.png"))

    docs_assets = os.path.join(root, "docs", "assets")
    os.makedirs(docs_assets, exist_ok=True)
    big.save(os.path.join(docs_assets, "logo.png"))

    big.save(os.path.join(assets, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icon written:", os.path.join(assets, "icon.ico"))


if __name__ == "__main__":
    main()
