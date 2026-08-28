"""Render bundled Fluent glyphs and official VS Code artwork into runtime PNGs."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "tools" / "ui_sources"
OUTPUT = ROOT / "assets" / "ui"
SCALE = 4

ICONS = {
    "update": (61758, "#625DF5", 28),
    "status": (59869, "#625DF5", 28),
    "diagnostics": (63269, "#625DF5", 28),
    "uninstall": (62285, "#D23F4B", 28),
    "test": (59802, "#625DF5", 28),
    "play-white": (62982, "#FFFFFF", 26),
    "lock-white": (59279, "#E4EBFB", 20),
    "check-green": (62104, "#16835D", 18),
}


def render_glyph(codepoint: int, color: str, size: int) -> Image.Image:
    canvas_size = size * SCALE
    font = ImageFont.truetype(str(SOURCE / "FluentSystemIcons-Regular.ttf"), int(canvas_size * 0.82))
    image = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    glyph = chr(codepoint)
    bounds = draw.textbbox((0, 0), glyph, font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    position = ((canvas_size - width) / 2 - bounds[0], (canvas_size - height) / 2 - bounds[1])
    draw.text(position, glyph, font=font, fill=color)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, (codepoint, color, size) in ICONS.items():
        render_glyph(codepoint, color, size).save(OUTPUT / f"{name}.png", optimize=True)
    with Image.open(SOURCE / "vscode.png") as vscode:
        vscode.convert("RGBA").resize((28, 28), Image.Resampling.LANCZOS).save(
            OUTPUT / "vscode.png", optimize=True,
        )


if __name__ == "__main__":
    main()
