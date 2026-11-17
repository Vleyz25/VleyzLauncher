"""Страницы лаунчера.

Каждая страница — отдельный модуль с функцией ``draw_page(app)``. Приложение
зовёт её при перерисовке и не знает, как страница устроена внутри. Новая
страница = новый файл здесь и одна строка в ``PAGES``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..app import Launcher

from . import about, builder, library, modpacks, mods  # noqa: E402

PAGES: dict[str, Callable[["Launcher"], None]] = {
    "about": about.draw_page,
    "library": library.draw_page,
    "mods": mods.draw_page,
    "builder": builder.draw_page,
    "modpacks": modpacks.draw_page,
}


def draw_detail(app: "Launcher", *bounds) -> None:
    """Карточка мода рисуется внутри общей модалки приложения."""
    mods.draw_detail(app, *bounds)


def draw_pack_detail(app: "Launcher", *bounds) -> None:
    """Карточка модпака."""
    modpacks.draw_detail(app, *bounds)


def draw(page: str, app: "Launcher") -> bool:
    """Рисует страницу, если она вынесена в модуль. Иначе отдаёт False."""
    painter = PAGES.get(page)
    if painter is None:
        return False
    painter(app)
    return True
