"""Страница «Сборки»: каталог модпаков Modrinth и установка в один клик.

Сборка ставится в отдельный инстанс, ничего не перезаписывая. Версии
показываются списком: у паков своя версия игры и загрузчик, поэтому
фильтры по текущей сборке здесь не применяются.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

from ... import i18n
from ...core import catalog, instances, loaders, modpack_source, modpacks, modrinth, paths
from .. import draw, theme
from ..layout import CX0, CX1
from ..widgets import CanvasButton

if TYPE_CHECKING:
    from ..app import Launcher

ROW_H = 74
MAX_ROWS = 5


def state(app: "Launcher") -> dict:
    data = getattr(app, "_modpacks_state", None)
    if data is None:
        data = {
            "query": "",
            "category": "",
            "sort": "downloads",
            "page": 0,
            "items": [],
            "total": 0,
            "pages": 1,
            "busy": False,
            "loaded": False,
            "error": "",
            "status": "",
            "detail": None,
            "detail_busy": False,
            "versions": [],
            "installing": "",
            "install_status": "",
            "view": "catalog",
        }
        app._modpacks_state = data
    return data


def _short(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _two_lines(value: object, width: int = 70) -> list[str]:
    words = " ".join(str(value or "").split()).split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
        if len(lines) == 2:
            break
    if current and len(lines) < 2:
        lines.append(current)
    return lines or [""]


def draw_page(app: "Launcher") -> None:
    cv = app.canvas
    data = state(app)
    app._header("Сборки сообщества", "Готовые модпаки Modrinth · установка в один клик")

    view = data.get("view") or "catalog"
    catalog_btn = CanvasButton(app, CX1 - 284, 74, CX1 - 156, 104, "Каталог",
                               command=lambda: _switch_view(app, "catalog"),
                               kind="primary" if view == "catalog" else "ghost", size=8)
    installed_btn = CanvasButton(app, CX1 - 148, 74, CX1 - 24, 104, "Установленные",
                                 command=lambda: _switch_view(app, "installed"),
                                 kind="primary" if view == "installed" else "ghost", size=8)
    app.page_buttons.extend([catalog_btn, installed_btn])
    if view == "installed":
        _draw_installed(app, data)
        return

    draw.rr(cv, CX0, 116, CX0 + 566, 152, 10, fill=theme.FIELD, outline=theme.BORDER,
            tags="page")
    app.entries.get("mp_query") or app._entry("mp_query", str(data["query"]))
    app._place_entry("mp_query", CX0, 116, 566, 36)
    entry = app.entries["mp_query"]
    entry.bind("<Return>", lambda _e: _search(app, reset=True))
    find = CanvasButton(app, CX0 + 578, 116, CX0 + 700, 152, "Искать",
                        command=lambda: _search(app, reset=True), kind="primary", size=9,
                        enabled=not data["busy"])
    clear = CanvasButton(app, CX0 + 708, 116, CX0 + 844, 152, "Сброс",
                         command=lambda: _reset(app), kind="ghost", size=9)
    app.page_buttons.extend([find, clear])

    category = (catalog.pack_category_label(data["category"])
                if data["category"] else "Все категории")
    sort = catalog.sort_label(data["sort"])
    cat_btn = CanvasButton(app, CX0, 160, CX0 + 300, 190,
                           f"{i18n.tr('Категория')}: {i18n.tr(category)}",
                           command=lambda: _cycle(app, "category"), kind="ghost", size=8)
    sort_btn = CanvasButton(app, CX0 + 308, 160, CX0 + 560, 190,
                            f"{i18n.tr('Сорт')}: {i18n.tr(sort)}",
                            command=lambda: _cycle(app, "sort"), kind="ghost", size=8)
    app.page_buttons.extend([cat_btn, sort_btn])

    status_y = 206
    if data["error"]:
        draw.text(cv, CX0, status_y, _short(data["error"], 100), 8, theme.DANGER,
                  "normal", "w", "page")
    elif data["busy"]:
        draw.text(cv, CX0, status_y, "Загружаю сборки…", 8, theme.MUTED, "normal", "w",
                  "page")
    elif data["loaded"]:
        draw.text(cv, CX0, status_y,
                  f"{i18n.tr('Найдено')}: {data['total']} · {i18n.tr('страница')} "
                  f"{data['page'] + 1} {i18n.tr('из')} {data['pages']}",
                  8, theme.MUTED, "normal", "w", "page")
    else:
        draw.text(cv, CX0, status_y, "Выберите сборку и посмотрите версии.", 8, theme.DIM,
                  "normal", "w", "page")

    items = data["items"]
    if not items and data["loaded"] and not data["busy"]:
        draw.rr(cv, CX0, 226, CX1, 320, 16, fill=theme.CARD, outline=theme.BORDER,
                tags="page")
        draw.text(cv, CX0 + 26, 258, "Сборок не нашлось", 12, theme.TEXT, "bold", "w",
                  "page")
        draw.text(cv, CX0 + 26, 286, "Снимите фильтры или измените запрос.", 8, theme.MUTED,
                  "normal", "w", "page")

    y = 226
    for item in items[:MAX_ROWS]:
        tag = f"mp_row_{item['id'][:10]}"
        draw.rr(cv, CX0, y, CX1, y + ROW_H - 8, 14, fill=theme.CARD, outline=theme.BORDER,
                tags=("page", tag))
        draw.cube(cv, CX0 + 36, y + 33, 24, theme.ACCENT, tags=("page", tag))
        draw.text(cv, CX0 + 62, y + 14, _short(item["title"], 46), 10, theme.TEXT, "bold",
                  "w", ("page", tag))
        for index, line in enumerate(_two_lines(item.get("description"))):
            draw.text(cv, CX0 + 62, y + 33 + index * 14, line, 8, theme.MUTED, "normal",
                      "w", ("page", tag))
        downloads = f"{int(item.get('downloads') or 0):,}".replace(",", " ")
        draw.text(cv, CX1 - 462, y + 20, f"{downloads} {i18n.tr('загрузок')}", 8,
                  theme.DIM, "normal", "w", ("page", tag))
        button = CanvasButton(app, CX1 - 152, y + 18, CX1 - 24, y + 54, "Версии",
                              command=lambda it=item: _open_detail(app, it), kind="ghost",
                              size=9, enabled=not data["busy"])
        app.page_buttons.append(button)
        cv.tag_bind(tag, "<Button-1>", lambda _e, it=item: _open_detail(app, it))
        y += ROW_H

    if data["loaded"] and data["pages"] > 1:
        prev = CanvasButton(app, CX1 - 96, 606, CX1 - 64, 636, "←",
                            command=lambda: _go(app, -1), kind="ghost", size=9,
                            enabled=data["page"] > 0 and not data["busy"])
        nxt = CanvasButton(app, CX1 - 58, 606, CX1 - 26, 636, "→",
                           command=lambda: _go(app, 1), kind="ghost", size=9,
                           enabled=data["page"] + 1 < data["pages"] and not data["busy"])
        app.page_buttons.extend([prev, nxt])

    if not data["loaded"] and not data["busy"] and not data["error"]:
        app.after(60, lambda: _search(app, reset=True))


def _reset(app: "Launcher") -> None:
    data = state(app)
    data.update(category="", sort="downloads", page=0)
    entry = app.entries.get("mp_query")
    if entry is not None:
        entry.delete(0, "end")
    data["query"] = ""
    _search(app, reset=True)


def _cycle(app: "Launcher", field: str) -> None:
    data = state(app)
    if field == "category":
        keys = [key for key, _label in catalog.PACK_CATEGORIES]
        data["category"] = keys[(keys.index(data["category"]) + 1) % len(keys)] \
            if data["category"] in keys else keys[1]
    else:
        keys = [key for key, _label in catalog.SORTS]
        data["sort"] = keys[(keys.index(data["sort"]) + 1) % len(keys)]
    data["page"] = 0
    _search(app, reset=True)


def _go(app: "Launcher", delta: int) -> None:
    data = state(app)
    data["page"] = max(0, min(data["pages"] - 1, data["page"] + delta))
    _search(app, reset=False)


def _search(app: "Launcher", reset: bool) -> None:
    data = state(app)
    if data["busy"]:
        return
    entry = app.entries.get("mp_query")
    if entry is not None:
        data["query"] = entry.get().strip()
    if reset:
        data["page"] = 0
    data.update(busy=True, error="", install_status="")
    app._show_page("modpacks")

    query = data["query"]
    page = data["page"]
    category = data["category"]
    sort = data["sort"]

    def work() -> None:
        try:
            result = catalog.search_packs(query, "", "", category, sort, page)
            error = ""
        except modrinth.ModrinthError as exc:
            result = {"items": [], "total": 0, "pages": 1, "page": page}
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - показываем причину
            result = {"items": [], "total": 0, "pages": 1, "page": page}
            error = str(exc)
        data.update(items=result["items"], total=result["total"], pages=result["pages"],
                    page=result["page"], busy=False, loaded=True, error=error)
        app._post(lambda: app._show_page("modpacks") if app.page == "modpacks" else None)

    threading.Thread(target=work, daemon=True, name="modpack-search").start()


def _open_detail(app: "Launcher", item: dict) -> None:
    data = state(app)
    data.update(detail=item, detail_busy=True, versions=[], install_status="")
    app.modal = "modpack"
    app._hide_entries()
    app._draw_modal()

    def work() -> None:
        try:
            versions = catalog.project_versions(str(item["id"]))
            error = ""
        except Exception as exc:  # noqa: BLE001
            versions, error = [], str(exc)
        data.update(versions=versions[:6], detail_busy=False)
        if error:
            data["install_status"] = error[:120]
        app._post(lambda: app._draw_modal() if app.modal == "modpack" else None)

    threading.Thread(target=work, daemon=True, name="modpack-versions").start()


def draw_detail(app: "Launcher", px0, px1, py0, py1) -> None:
    cv = app.canvas
    data = state(app)
    item = data.get("detail") or {}
    close = CanvasButton(app, px1 - 62, py0 + 20, px1 - 24, py0 + 52, "×",
                         command=app._close_modal, kind="ghost", size=12)
    app.modal_buttons.append(close)

    draw.text(cv, px0 + 28, py0 + 32, _short(item.get("title"), 42), 14, theme.TEXT,
              "bold", "w", "modal")
    meta = f"{int(item.get('downloads') or 0):,} {i18n.tr('загрузок')}".replace(",", " ")
    draw.text(cv, px0 + 28, py0 + 56, meta, 8, theme.MUTED, "normal", "w", "modal")
    draw.text(cv, px0 + 28, py0 + 78, _short(item.get("description"), 150), 8,
              theme.MUTED, "normal", "w", "modal", width=px1 - px0 - 56)

    draw.text(cv, px0 + 28, py0 + 128, "Версии сборки", 9, theme.TEXT, "bold", "w",
              "modal")
    if data["detail_busy"]:
        draw.text(cv, px0 + 28, py0 + 152, "Загружаю версии…", 8, theme.MUTED, "normal",
                  "w", "modal")
    versions = data.get("versions") or []
    if not versions and not data["detail_busy"]:
        draw.text(cv, px0 + 28, py0 + 152, "Версий нет.", 8, theme.DIM, "normal", "w",
                  "modal")
    for index, row in enumerate(versions[:4]):
        y = py0 + 148 + index * 52
        draw.rr(cv, px0 + 28, y, px1 - 28, y + 40, 9, fill=theme.CARD,
                outline=theme.BORDER, tags="modal")
        label = f"{_short(row['version_number'] or row['name'], 24)}"
        draw.text(cv, px0 + 40, y + 12, label, 8, theme.TEXT, "normal", "w", "modal")
        games = ", ".join(row["game_versions"][:3])
        loaders = ", ".join(row["loaders"][:2])
        draw.text(cv, px0 + 40, y + 27, f"{games} · {loaders}", 7, theme.DIM, "normal",
                  "w", "modal")
        installing = data["installing"] == row["id"]
        button = CanvasButton(app, px1 - 140, y + 6, px1 - 36, y + 34,
                              "Устанавливаю…" if installing else "Установить",
                              command=lambda r=row: _install(app, r), kind="primary",
                              size=8, enabled=not installing)
        app.modal_buttons.append(button)

    if data.get("install_status"):
        draw.text(cv, px0 + 28, py1 - 76, _short(data["install_status"], 84), 8,
                  theme.ACCENT, "normal", "w", "modal")
    done = CanvasButton(app, px1 - 138, py1 - 58, px1 - 24, py1 - 20, "Закрыть",
                        command=app._close_modal, kind="ghost", size=9)
    app.modal_buttons.append(done)


def _switch_view(app: "Launcher", view: str) -> None:
    data = state(app)
    if (data.get("view") or "catalog") == view:
        return
    data["view"] = view
    app._show_page("modpacks")


def _draw_installed(app: "Launcher", data: dict) -> None:
    cv = app.canvas
    rows = modpacks.installed_packs_all(app._data_dir())
    draw.text(cv, CX0, 116, f"Установлено сборок: {len(rows)}", 9, theme.MUTED,
              "normal", "w", "page")
    if not rows:
        draw.rr(cv, CX0, 146, CX1, 246, 16, fill=theme.CARD, outline=theme.BORDER,
                tags="page")
        draw.text(cv, CX0 + 26, 176, "Установленных сборок нет", 12, theme.TEXT, "bold",
                  "w", "page")
        draw.text(cv, CX0 + 26, 204,
                  "Откройте «Каталог» и установите любую — она появится здесь.",
                  8, theme.MUTED, "normal", "w", "page")
        return
    y = 146
    for row in rows[:MAX_ROWS]:
        draw.rr(cv, CX0, y, CX1, y + 60, 14, fill=theme.CARD, outline=theme.BORDER,
                tags="page")
        name = f"{row['name']} {row['version']}".strip()
        draw.text(cv, CX0 + 26, y + 14, _short(name, 52), 10, theme.TEXT, "bold", "w",
                  "page")
        state = "включена" if row.get("enabled", True) else "отключена"
        meta = f"{row['game']} · {row['loader']} · {state} · {row['instance']}"
        draw.text(cv, CX0 + 26, y + 36, _short(meta, 76), 8, theme.MUTED, "normal", "w",
                  "page")
        toggle = CanvasButton(
            app, CX1 - 372, y + 12, CX1 - 246, y + 48,
            "Отключить" if row.get("enabled", True) else "Включить",
            command=lambda it=row: _toggle_pack(app, it), kind="ghost", size=9)
        choose = CanvasButton(app, CX1 - 240, y + 12, CX1 - 132, y + 48, "Выбрать",
                              command=lambda it=row: _use_instance(app, it),
                              kind="primary", size=9)
        folder = CanvasButton(app, CX1 - 120, y + 12, CX1 - 24, y + 48, "Папка",
                              command=lambda it=row: _open_instance(app, it),
                              kind="ghost", size=9)
        app.page_buttons.extend([toggle, choose, folder])
        y += 68


def _use_instance(app: "Launcher", row: dict) -> None:
    instance_id = str(row.get("instance") or "")
    for item in loaders.installed_versions(app._data_dir()):
        if item["id"] == instance_id:
            app._select_installed(item)
            return
    app._push_log("Для этой сборки не установлена версия игры — установите её заново.")


def _open_instance(app: "Launcher", row: dict) -> None:
    folder = instances.path(app._data_dir(), str(row.get("instance") or ""))
    try:
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))  # noqa: S606 - открытие проводника
    except OSError as exc:
        app._push_log(f"Папка сборки не открылась: {exc}")


def _toggle_pack(app: "Launcher", row: dict) -> None:
    enabled = not bool(row.get("enabled", True))
    try:
        modpacks.set_pack_enabled(app._data_dir(), str(row.get("instance") or ""),
                                  enabled, log=app._push_log)
    except OSError as exc:
        app._push_log(f"Не удалось переключить сборку: {exc}")
    app._show_page("modpacks")


def _install(app: "Launcher", version: dict) -> None:
    data = state(app)
    if data["installing"]:
        return
    data.update(installing=str(version.get("id") or ""), install_status="Устанавливаю…")
    app._draw_modal()
    data_dir = app._data_dir()
    log = app._push_log_threadsafe

    def work() -> None:
        result = None
        status = ""
        try:
            result = modpack_source.install(version, data_dir, log=log)
            status = f"Готово: {result['instance']} · {result.get('pack_name') or ''}"
        except Exception as exc:  # noqa: BLE001 - показываем причину
            status = f"Ошибка: {exc}"
        data.update(installing="", install_status=_short(status, 110))
        if result is not None:
            game = str(result.get("game_version") or app.game)
            loader = str(result.get("loader") or "vanilla")
            version_id = str(result.get("instance") or "")
            app.game = game
            app.loader = loader if loader in loaders.LOADER_BY_ID else "vanilla"
            if version_id:
                app.installed_id = version_id
            app.cfg["selected_version"] = game
            app.cfg["selected_loader"] = app.loader
            try:
                paths.save_config(app.cfg)
            except OSError:
                pass
            app._post(app._scan_installed)
        app._post(lambda: app._draw_modal() if app.modal == "modpack" else None)

    threading.Thread(target=work, daemon=True, name="modpack-install").start()
