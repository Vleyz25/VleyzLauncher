"""Страница «О лаунчере»."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ... import APP_VERSION
from .. import draw, theme
from ..layout import CX0, CX1
from ..widgets import CanvasButton

if TYPE_CHECKING:
    from ..app import Launcher


def draw_page(app: "Launcher") -> None:
    cv = app.canvas
    app._header("О лаунчере", "Чистый лаунчер: ничего не предустановлено")
    draw.rr(cv, CX0, 132, CX1, 648, 18, fill=theme.PANEL, outline=theme.PANEL_BORDER, tags="page")
    draw.cube(cv, CX0 + 76, 196, 64, theme.ACCENT, tags="page")
    draw.text(cv, CX0 + 124, 176, f"Vleyz Launcher {APP_VERSION}", 16, theme.TEXT, "bold",
              "w", "page")
    draw.text(cv, CX0 + 124, 200, "Сделал vleyz", 13, theme.ACCENT, "bold", "w", "page")
    draw.text(cv, CX0 + 124, 222, "Любая версия Minecraft, любой загрузчик — в пару кликов.",
              9, theme.MUTED, "normal", "w", "page")

    points = [
        ("Все версии из манифеста",
         "Список релизов Mojang подтягивается онлайн: поиск по id, страницы."),
        ("Fabric, Forge, NeoForge",
         "Fabric ставится профилем, Forge и NeoForge — их официальными установщиками."),
        ("Проверка целостности",
         "Каждый файл сверяется по SHA1, битые и подменённые загрузки отбрасываются."),
        ("Java сама",
         "Если нужной Java нет, лаунчер скачает официальную сборку Adoptium."),
        ("Своя папка игры",
         "Профиль живёт отдельно от .minecraft, ваши миры и настройки не затрагиваются."),
    ]
    y = 272
    for title, body in points:
        draw.dot(cv, CX0 + 36, y + 8, 3, theme.ACCENT, tags="page")
        draw.text(cv, CX0 + 50, y, title, 10, theme.TEXT, "bold", "w", "page")
        draw.text(cv, CX0 + 50, y + 18, body, 9, theme.MUTED, "normal", "w", "page")
        y += 46

    draw.hline(cv, CX0 + 28, CX1 - 28, 520, theme.DIVIDER, tags="page")
    draw.text(cv, CX0 + 28, 540,
              "Minecraft — торговая марка Mojang Studios. Лаунчер не связан с Mojang.",
              8, theme.DIM, "normal", "w", "page")
    draw.text(cv, CX0 + 28, 560,
              "Играть можно только на аккаунте, который принадлежит вам (EULA Mojang).",
              8, theme.DIM, "normal", "w", "page")
    check = CanvasButton(app, CX0 + 28, 580, CX0 + 268, 620, "Проверить обновления",
                         command=lambda: app._check_updates(manual=True), kind="ghost", size=9)
    app.page_buttons.append(check)
    draw.text(cv, CX1 - 28, 600, "Сделал vleyz", 9, theme.DIM, "normal", "e", "page")
