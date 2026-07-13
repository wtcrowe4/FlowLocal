"""Generates a high-res FlowLocal icon (icon.ico + icon.png).
Dark rounded-square tile, glowing electric-blue mic. Run via create_shortcut.bat."""
from PIL import Image, ImageDraw, ImageFilter

S = 512
ACCENT = (0, 168, 255)
ACCENT_HI = (120, 210, 255)

# ---- dark gradient tile with rounded corners
tile = Image.new("RGBA", (S, S))
td = ImageDraw.Draw(tile)
for y in range(S):
    t = y / S
    td.line([(0, y), (S, y)], fill=(int(20 + 14 * t), int(22 + 16 * t), int(27 + 22 * t), 255))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([6, 6, S - 6, S - 6], radius=112, fill=255)
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
img.paste(tile, (0, 0), mask)

# subtle top bevel edge
edge = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(edge).rounded_rectangle([6, 6, S - 6, S - 6], radius=112,
                                       outline=(90, 96, 110, 200), width=3)
img.alpha_composite(edge)

# ---- blue glow behind mic
glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(glow).rounded_rectangle([176, 96, 336, 300], radius=80,
                                       fill=ACCENT + (160,))
img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(48)))

# ---- mic
# capsule body: gradient rendered separately, pasted through rounded mask
body = Image.new("RGBA", (S, S), (0, 0, 0, 0))
bd = ImageDraw.Draw(body)
for i, y in enumerate(range(110, 290)):
    t = i / 180
    col = tuple(int(ACCENT_HI[c] + (ACCENT[c] - ACCENT_HI[c]) * t) for c in range(3))
    bd.line([(196, y), (316, y)], fill=col + (255,))
body_mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(body_mask).rounded_rectangle([196, 110, 316, 290], radius=60, fill=255)
img.paste(body, (0, 0), body_mask)
d = ImageDraw.Draw(img)
# grille lines
for gy in (160, 190, 220):
    d.line([(226, gy), (286, gy)], fill=(10, 20, 30, 160), width=8)
# cradle arc
d.arc([166, 170, 346, 350], start=20, end=160, fill=ACCENT, width=18)
# stem + base
d.line([(256, 348), (256, 400)], fill=ACCENT, width=18)
d.line([(206, 408), (306, 408)], fill=ACCENT, width=18)

img.save("icon.png")
img.save("icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon.ico + icon.png written")
