from __future__ import annotations

import math

from . import theme


def hex_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def mix(color_a: str, color_b: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    a, b = hex_rgb(color_a), hex_rgb(color_b)
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def rgba_over(bg: str, fg: str, alpha: float) -> str:
    """Имитация полупрозрачности: смешиваем цвет с фоном."""
    return mix(bg, fg, alpha)


def rr(cv, x0, y0, x1, y1, radius=12, fill=None, outline=None, width=1, tags=None, steps=10):
    """Скруглённый прямоугольник точной геометрией (дуги по 4 углам)."""
    radius = max(0.0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    if radius <= 0.5:
        return cv.create_rectangle(x0, y0, x1, y1, fill=fill or "", outline=outline or "", width=width, tags=tags)
    points: list[float] = []
    corners = (
        (x0 + radius, y0 + radius, 180, 270),
        (x1 - radius, y0 + radius, 270, 360),
        (x1 - radius, y1 - radius, 0, 90),
        (x0 + radius, y1 - radius, 90, 180),
    )
    for cx, cy, start, end in corners:
        for i in range(steps + 1):
            angle = math.radians(start + (end - start) * i / steps)
            points.extend((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return cv.create_polygon(
        points, fill=fill if fill else "", outline=outline if outline else "", width=width, tags=tags
    )


def gradient_vertical(cv, x0, y0, x1, y1, top, bottom, radius=0, tags=None):
    """Вертикальный градиент с честными скруглениями по краям."""
    items = []
    top_y, bottom_y = int(round(y0)), int(round(y1))
    span = max(1, bottom_y - top_y - 1)
    for y in range(top_y, bottom_y):
        t = (y - y0) / span
        inset = 0.0
        if radius > 0:
            if y < y0 + radius:
                delta = (y0 + radius) - (y + 0.5)
                inset = radius - math.sqrt(max(0.0, radius * radius - delta * delta))
            elif y >= y1 - radius:
                delta = (y + 0.5) - (y1 - radius)
                inset = radius - math.sqrt(max(0.0, radius * radius - delta * delta))
        items.append(
            cv.create_line(x0 + inset, y + 0.5, x1 - inset, y + 0.5, fill=mix(top, bottom, t), tags=tags)
        )
    return items


def gradient_band(cv, x0, y0, x1, y1, left, right, steps=120, tags=None):
    """Горизонтальный градиент (используется для прогресс-бара)."""
    items = []
    width = max(1.0, x1 - x0)
    for i in range(steps):
        xa = x0 + width * i / steps
        xb = x0 + width * (i + 1) / steps + 0.6
        items.append(cv.create_line(xa, y0, xb, y0, fill=mix(left, right, i / steps), width=max(1.0, y1 - y0), tags=tags))
    return items


def text(cv, x, y, value, size=10, color=theme.TEXT, weight="normal", anchor="w", tags=None,
         family=theme.FONT, justify="left", width=0):
    return cv.create_text(
        x, y, text=value, fill=color, anchor=anchor, justify=justify, width=width or 0,
        font=(family if weight == "normal" else theme.FONT_SEMI, size, weight if weight != "normal" else "normal"),
        tags=tags,
    )


def dot(cv, x, y, r, color, tags=None):
    return cv.create_oval(x - r, y - r, x + r, y + r, fill=color, outline="", tags=tags)


def chip(cv, x, y, label, color, tags=None, size=8, pad_x=9, height=22, anchor="w", bg=None):
    """Небольшой бейдж. Возвращает (id_фона, id_текста, ширина)."""
    approx = len(label) * size * 0.58 + pad_x * 2
    if anchor == "e":
        x0 = x - approx
    else:
        x0 = x
    bg_id = rr(cv, x0, y - height / 2, x0 + approx, y + height / 2, height / 2,
               fill=bg or mix(theme.CARD_BOTTOM, color, 0.14),
               outline=mix(theme.CARD_BOTTOM, color, 0.3), width=1, tags=tags)
    text_id = cv.create_text(x0 + approx / 2, y, text=label, fill=color, anchor="center",
                             font=(theme.FONT_SEMI, size), tags=tags)
    return bg_id, text_id, approx


def cube(cv, cx, cy, size, base, tags=None, top_light=0.34, side_dark=0.3):
    """Изометрический блок — логотип и иконки версий."""
    half = size / 2
    quarter = size / 4
    top = [(cx, cy - half), (cx + half, cy - quarter), (cx, cy), (cx - half, cy - quarter)]
    left = [(cx - half, cy - quarter), (cx, cy), (cx, cy + half), (cx - half, cy + quarter)]
    right = [(cx, cy), (cx + half, cy - quarter), (cx + half, cy + quarter), (cx, cy + half)]
    ids = [
        cv.create_polygon(top, fill=mix(base, "#FFFFFF", top_light), outline="", tags=tags),
        cv.create_polygon(left, fill=mix(base, "#000000", side_dark), outline="", tags=tags),
        cv.create_polygon(right, fill=mix(base, "#000000", 0.12), outline="", tags=tags),
    ]
    return ids


def hline(cv, x0, x1, y, color, tags=None):
    return cv.create_line(x0, y, x1, y, fill=color, tags=tags)
