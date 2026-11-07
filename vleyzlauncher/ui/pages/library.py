"""Страница «Библиотека»: установленные версии, запуск и удаление."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core import instances as instances_core
from ...core import loaders, paths
from .. import draw, theme
from ..layout import CX0, CX1
from ..widgets import CanvasButton

if TYPE_CHECKING:
    from ..app import Launcher


def draw_page(app: "Launcher") -> None:
    cv = app.canvas
    app._header("Библиотека", "Все установленные версии · запуск в один клик")
    data_dir = app._data_dir()
    items = loaders.installed_versions(data_dir)
    per_page = 5
    pages = max(1, (len(items) + per_page - 1) // per_page)
    app.library_offset = min(app.library_offset, pages - 1)
    chunk = items[app.library_offset * per_page:(app.library_offset + 1) * per_page]

    if not items:
        draw.rr(cv, CX0, 140, CX1, 250, 16, fill=theme.CARD_BOTTOM, outline=theme.BORDER,
                tags="page")
        draw.text(cv, CX0 + 28, 176, "Пока ничего не установлено", 12, theme.TEXT, "bold",
                  "w", "page")
        draw.text(cv, CX0 + 28, 202, "Выберите версию на странице «Играть» и нажмите «Скачать».",
                  9, theme.MUTED, "normal", "w", "page")

    y = 140
    for item in chunk:
        vid = item["id"]
        accent = theme.loader_accent(item["loader"])
        launching = app.launching_id == vid
        running = app.running_id == vid
        selected = app.installed_id == vid

        draw.rr(cv, CX0, y, CX1, y + 92, 16,
                fill=theme.SELECT_BG if selected else theme.CARD,
                outline=accent if selected else theme.BORDER, width=1 if selected else 2,
                tags="page")
        draw.cube(cv, CX0 + 44, y + 46, 34, accent, tags="page")
        draw.text(cv, CX0 + 80, y + 26, f"Minecraft {item['game']}", 13, theme.TEXT, "bold",
                  "w", "page")
        draw.text(cv, CX0 + 80, y + 47, f"{item['label']} · {item['id']}", 9, theme.MUTED,
                  "normal", "w", "page")

        status_color = theme.ACCENT if (running or selected) else theme.MUTED
        status_text = ("запущена" if running else
                       "запускаю…" if launching else
                       f"установлена · {paths.human_size(item['size'])}")
        draw.dot(cv, CX0 + 84, y + 68, 3, status_color, tags="page")
        draw.text(cv, CX0 + 94, y + 68, status_text, 8, status_color, "normal", "w", "page")

        instance_path = instances_core.path(data_dir, vid)
        path_text = str(instance_path if instance_path.is_dir() else item["path"])
        if len(path_text) > 56:
            path_text = "…" + path_text[-55:]
        draw.text(cv, CX0 + 320, y + 68, path_text, 8, theme.DIM, "normal", "w", "page")

        if running or launching:
            play_btn = CanvasButton(app, CX1 - 352, y + 26, CX1 - 250, y + 66, "Запущена",
                                    command=None, kind="ghost", size=9, enabled=False)
        else:
            play_btn = CanvasButton(app, CX1 - 352, y + 26, CX1 - 250, y + 66, "Играть",
                                    command=lambda it=item: app._start_game(it["id"]),
                                    kind="primary", size=9)
        open_btn = CanvasButton(app, CX1 - 238, y + 26, CX1 - 132, y + 66, "Открыть",
                                command=lambda v=vid: app._open_version_folder(v),
                                kind="ghost", size=9)
        label = "Точно?" if app._confirm_delete == vid else "Удалить"
        del_btn = CanvasButton(app, CX1 - 120, y + 26, CX1 - 24, y + 66, label,
                               command=lambda v=vid: app._ask_delete(v), kind="danger", size=9)
        app.page_buttons.extend([play_btn, open_btn, del_btn])
        y += 100

    draw.text(cv, CX0, y + 14, f"Папка сборок: {data_dir / 'instances'}"[:110], 8, theme.DIM,
              "normal", "w", "page")
    if pages > 1:
        draw.text(cv, CX1 - 90, y + 14, f"стр. {app.library_offset + 1} / {pages}", 8,
                  theme.MUTED, "normal", "e", "page")
        prev = CanvasButton(app, CX1 - 84, y - 4, CX1 - 54, y + 26, "←",
                            command=lambda: app._library_go(-1), kind="ghost", size=9,
                            enabled=app.library_offset > 0)
        nxt = CanvasButton(app, CX1 - 50, y - 4, CX1 - 20, y + 26, "→",
                           command=lambda: app._library_go(1), kind="ghost", size=9,
                           enabled=app.library_offset < pages - 1)
        app.page_buttons.extend([prev, nxt])
