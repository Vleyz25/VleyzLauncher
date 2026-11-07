"""Кнопка на канве: прямоугольник со скруглением и подпись.

Живёт отдельно от приложения, чтобы страницы могли создавать кнопки, не
импортируя сам `app` (иначе получится циклический импорт).
"""

from __future__ import annotations

from .. import i18n
from . import draw, theme


class CanvasButton:
    _seq = 0

    def __init__(self, app, x0, y0, x1, y1, label, command=None, kind="primary", size=10,
                 enabled=True):
        CanvasButton._seq += 1
        self.tag = f"btn{CanvasButton._seq}"
        self.app = app
        self.box = (x0, y0, x1, y1)
        self.kind = kind
        self.command = command
        self.enabled = enabled
        self.hover = False
        self.cv = app.canvas
        self.bg = self._bg()
        self.radius = min(12, (y1 - y0) / 2)
        self.bg_id = draw.rr(self.cv, x0, y0, x1, y1, self.radius, fill=self.bg,
                             outline=self._outline(), width=1, tags=self.tag)
        self.label_id = self.cv.create_text(
            (x0 + x1) / 2, (y0 + y1) / 2, text=i18n.tr(label), tags=self.tag, anchor="center",
            font=(theme.FONT_SEMI, size), fill=self._fg(),
        )
        self.cv.tag_bind(self.tag, "<Enter>", self._enter)
        self.cv.tag_bind(self.tag, "<Leave>", self._leave)
        self.cv.tag_bind(self.tag, "<Button-1>", self._click)

    def _bg(self):
        if self.kind == "primary":
            return theme.ACCENT if self.enabled else theme.PRIMARY_OFF
        if self.kind == "danger":
            return theme.DANGER_BG
        return theme.CARD

    def _fg(self):
        if self.kind == "primary":
            return theme.ON_ACCENT if self.enabled else theme.DIM
        if self.kind == "danger":
            return theme.DANGER if self.enabled else theme.DIM
        return theme.TEXT if self.enabled else theme.DIM

    def _outline(self):
        if self.kind == "danger":
            return theme.DANGER_BORDER
        return theme.BORDER if self.kind != "primary" else ""

    def _enter(self, _event=None):
        if not self.enabled:
            return
        self.hover = True
        fill = {
            "primary": draw.mix(theme.ACCENT, "#FFFFFF", 0.14),
            "danger": theme.DANGER_HOVER,
        }.get(self.kind, theme.BUTTON_HOVER)
        self.cv.itemconfigure(self.bg_id, fill=fill)
        self.cv.configure(cursor="hand2")

    def _leave(self, _event=None):
        self.hover = False
        self.cv.itemconfigure(self.bg_id, fill=self._bg())
        self.cv.configure(cursor="")

    def _click(self, _event=None):
        if self.enabled and self.command:
            self.command()

    def set_enabled(self, enabled: bool):
        if self.enabled == enabled:
            return
        self.enabled = enabled
        self.cv.itemconfigure(self.bg_id, fill=self._bg(), outline=self._outline())
        self.cv.itemconfigure(self.label_id, fill=self._fg())

    def set_label(self, label: str):
        self.cv.itemconfigure(self.label_id, text=i18n.tr(label))

    def set_kind(self, kind: str):
        self.kind = kind
        self.cv.itemconfigure(self.bg_id, fill=self._bg(), outline=self._outline())
        self.cv.itemconfigure(self.label_id, fill=self._fg())

    def destroy(self):
        self.cv.delete(self.tag)
