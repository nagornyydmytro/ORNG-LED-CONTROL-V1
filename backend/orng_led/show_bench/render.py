"""Draw one decoded engine frame. 640×360, so bar segments and head aim stay visible."""

from __future__ import annotations

import numpy as np

WIDTH = 640
HEIGHT = 360
_STAGE = (36, 28, 568, 268)

# 3×5 uppercase. Missing glyphs are skipped.
_FONT = {
    "A": "111101111101101",
    "B": "110101110101110",
    "C": "111100100100111",
    "D": "110101101101110",
    "E": "111100110100111",
    "F": "111100110100100",
    "G": "111100101101111",
    "H": "101101111101101",
    "I": "111010010010111",
    "K": "101101110101101",
    "L": "100100100100111",
    "M": "101111101101101",
    "N": "110101101101101",
    "O": "111101101101111",
    "P": "111101111100100",
    "R": "111101111101101",
    "S": "111100111001111",
    "T": "111010010010010",
    "U": "101101101101111",
    "V": "101101101101010",
    "W": "101101101111101",
    "Y": "101101010010010",
    "0": "111101101101111",
    "1": "010010010010010",
    "2": "111001111100111",
    "3": "111001111001111",
    "4": "101101111001001",
    "5": "111100111001111",
    "6": "111100111101111",
    "7": "111001001001001",
    "8": "111101111101111",
    "9": "111101111001111",
    "-": "000000111000000",
    ":": "000010000010000",
    ".": "000000000000010",
    "/": "001001010100100",
    " ": "000000000000000",
}


def _box(image: np.ndarray, x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]) -> None:
    h, w, _ = image.shape
    x0 = max(0, min(w, x0))
    x1 = max(0, min(w, x1))
    y0 = max(0, min(h, y0))
    y1 = max(0, min(h, y1))
    if x1 <= x0 or y1 <= y0:
        return
    image[y0:y1, x0:x1] = color


def _blend(image: np.ndarray, x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int], gain: float) -> None:
    h, w, _ = image.shape
    x0 = max(0, min(w, x0))
    x1 = max(0, min(w, x1))
    y0 = max(0, min(h, y0))
    y1 = max(0, min(h, y1))
    if x1 <= x0 or y1 <= y0:
        return
    paint = np.array(color, dtype=np.float32) * max(0.0, min(1.0, gain))
    patch = image[y0:y1, x0:x1].astype(np.float32)
    image[y0:y1, x0:x1] = np.clip(np.maximum(patch, paint), 0, 255).astype(np.uint8)


def draw_text(image: np.ndarray, x: int, y: int, text: str, color: tuple[int, int, int] = (230, 230, 230), scale: int = 2) -> None:
    cursor = x
    for char in text.upper():
        glyph = _FONT.get(char)
        if glyph is None:
            cursor += 4 * scale
            continue
        for row in range(5):
            bits = glyph[row * 3 : row * 3 + 3]
            for col, bit in enumerate(bits):
                if bit == "1":
                    _box(
                        image,
                        cursor + col * scale,
                        y + row * scale,
                        cursor + (col + 1) * scale,
                        y + (row + 1) * scale,
                        color,
                    )
        cursor += 4 * scale


def _map(x: float, y: float) -> tuple[int, int]:
    left, top, width, height = _STAGE
    return int(left + float(x) * width), int(top + float(y) * height)


def _line(image: np.ndarray, x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int], gain: float) -> None:
    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
    for step in range(steps + 1):
        x = int(x0 + (x1 - x0) * step / steps)
        y = int(y0 + (y1 - y0) * step / steps)
        _blend(image, x - 1, y - 1, x + 2, y + 2, color, gain)


def render_frame(
    view: object,
    placements: dict[str, object],
    *,
    clock_s: float,
    score: str,
    heard: str,
) -> np.ndarray:
    image = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    image[:, :] = (14, 16, 22)
    left, top, width, height = _STAGE
    _box(image, left, top, left + width, top + height, (24, 28, 36))
    mid = left + width // 2
    _box(image, mid, top, mid + 1, top + height, (46, 52, 64))
    by_id = placements
    for bar in getattr(view, "bars", ()):
        place = by_id.get(bar.id)
        if place is None:
            continue
        x, y = _map(float(place.x), float(place.y) - float(place.height) / 2.0)
        seg_h = max(4, int(float(place.height) * height / max(len(bar.segments), 1)))
        seg_w = max(8, int(float(place.width) * width) + 8)
        for index, level in enumerate(bar.segments):
            gain = max(0.0, min(1.0, float(level) * max(float(bar.dimmer), 0.15)))
            color = (
                int(max(0.0, min(1.0, bar.segment_r[index] if index < len(bar.segment_r) else bar.r)) * 255),
                int(max(0.0, min(1.0, bar.segment_g[index] if index < len(bar.segment_g) else bar.g)) * 255),
                int(max(0.0, min(1.0, bar.segment_b[index] if index < len(bar.segment_b) else bar.b)) * 255),
            )
            if color == (0, 0, 0):
                color = (255, 140, 40)
            _blend(image, x - seg_w // 2, y + index * seg_h, x + seg_w // 2, y + (index + 1) * seg_h - 1, color, gain)
    for par in list(getattr(view, "pars", ())) + list(getattr(view, "faces", ())):
        place = by_id.get(par.id)
        if place is None:
            continue
        x, y = _map(float(place.x), float(place.y))
        gain = max(0.0, min(1.0, float(par.intensity)))
        radius = 7 + int(8 * gain)
        color = (int(par.r * 255), int(par.g * 255), int(par.b * 255))
        _blend(image, x - radius, y - radius, x + radius, y + radius, color or (255, 180, 60), max(gain, 0.08))
    for beam in getattr(view, "beams", ()):
        place = by_id.get(beam.id)
        if place is None:
            continue
        x, y = _map(float(place.x), float(place.y))
        pan = float(beam.pan) - 0.5
        tilt = float(beam.tilt)
        reach = 36 + int(70 * max(0.15, min(1.0, tilt)))
        # Audience is toward the bottom of the plan. Opposite pan values open away from center.
        x1 = x + int(pan * 160)
        y1 = y + reach
        lamp = max(float(beam.r), float(beam.g), float(beam.b), float(beam.dimmer) * 0.15)
        color = (
            int(max(beam.r, 0.25 if lamp < 0.08 else 0.0) * 255),
            int(max(beam.g, 0.25 if lamp < 0.08 else 0.0) * 255),
            int(max(beam.b, 0.2 if lamp < 0.08 else 0.0) * 255),
        )
        _line(image, x, y, x1, y1, color, max(0.35, min(1.0, lamp if lamp > 0.02 else 0.45)))
        _blend(image, x - 4, y - 4, x + 5, y + 5, (220, 220, 230), 0.9)
    minutes = int(clock_s) // 60
    seconds = int(clock_s) % 60
    draw_text(image, 8, 8, f"{minutes:02d}:{seconds:02d}  {score}"[:42])
    draw_text(image, 8, HEIGHT - 28, heard[:52], color=(180, 210, 180))
    return image
