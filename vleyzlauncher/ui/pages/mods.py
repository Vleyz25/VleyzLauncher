"""Страница «Моды»: каталог Modrinth и уже установленные моды.

Каталог — это интернет-список: поиск, категории, сортировка, окружение и
страницы. Установка идёт в папку mods выбранной сборки вместе с
обязательными зависимостями. Второй режим страницы — прежний список
локальных файлов с включением и отключением.
"""

from __future__ import annotations

import threading
import webbrowser
from typing import TYPE_CHECKING

from ...core import catalog, loaders, modrinth, mods as mods_core, paths
from ... import i18n
from .. import draw, theme
from ..layout import CX0, CX1
from ..widgets import CanvasButton

if TYPE_CHECKING:
    from ..app import Launcher

CARD_W = 210
CARD_H = 148
COLS = 4
ROWS = 3
GAP = 10
GRID_TOP = 204
GRID_BOTTOM = GRID_TOP + ROWS * CARD_H + (ROWS - 1) * GAP

CATEGORY_ACCENTS = {
    "optimization": "#7BD88F",
    "technology": "#7C8CFA",
    "magic": "#B98CFA",
    "adventure": "#F2B36B",
    "decoration": "#F28CB1",
    "worldgen": "#6BC7D8",
    "mobs": "#E0A96D",
    "library": "#8FA0B8",
}


def state(app: "Launcher") -> dict:
    data = getattr(app, "_catalog_state", None)
    if data is None:
        data = {
            "query": "",
            "category": "",
            "sort": "relevance",
            "env": "",
            "page": 0,
            "items": [],
            "total": 0,
            "pages": 1,
            "busy": False,
            "loaded": False,
            "favorites_only": False,
            "offline": False,
            "error": "",
            "detail": None,
            "detail_busy": False,
            "detail_error": "",
            "installing": "",
            "install_status": "",
        }
        app._catalog_state = data
    return data


def _short(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _two_lines(value: object, width: int = 36) -> list[str]:
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


def _accent(item: dict) -> str:
    for slug in item.get("categories") or []:
        if slug in CATEGORY_ACCENTS:
            return CATEGORY_ACCENTS[slug]
    return theme.ACCENT


def _favorites(app: "Launcher") -> list[str]:
    values = app.cfg.get("favorites")
    return [str(item) for item in values] if isinstance(values, list) else []


def _is_favorite(app: "Launcher", project_id: str) -> bool:
    return str(project_id) in _favorites(app)


def _toggle_favorite(app: "Launcher", project_id: str) -> None:
    favorites = _favorites(app)
    marker = str(project_id)
    if marker in favorites:
        favorites.remove(marker)
    else:
        favorites.append(marker)
    app.cfg["favorites"] = favorites[:200]
    paths.save_config(app.cfg)
    app._show_page("mods")


def draw_page(app: "Launcher") -> None:
    if getattr(app, "mods_view", "catalog") == "installed":
        app._draw_mods()
        return
    _draw_catalog(app)


def _draw_catalog(app: "Launcher") -> None:
    cv = app.canvas
    data = state(app)
    game, loader = app._instance_meta()
    installable = bool((app.installed_id and loader in ("fabric", "forge", "neoforge"))
                       or getattr(app, "loader_instance_id", ""))
    subtitle = (
        f"{game} · {loader} · {i18n.tr('каталог Modrinth')}"
        if installable
        else i18n.tr("Каталог Modrinth · выберите сборку на «Играть», "
                     "чтобы ставить моды в один клик")
    )
    app._header("Каталог модов", subtitle)

    seg_y = 66
    catalog_btn = CanvasButton(app, CX1 - 244, seg_y, CX1 - 128, seg_y + 30, "Каталог",
                               command=lambda: _switch_view(app, "catalog"), kind="primary", size=8)
    installed_btn = CanvasButton(app, CX1 - 120, seg_y, CX1 - 4, seg_y + 30, "Установленные",
                                 command=lambda: _switch_view(app, "installed"), kind="ghost", size=8)
    app.page_buttons.extend([catalog_btn, installed_btn])

    instance_btn = CanvasButton(
        app, CX1 - 500, seg_y, CX1 - 260, seg_y + 30,
        f"{i18n.tr('Сборка')}: {app.installed_id or '—'}",
        command=lambda: _cycle_instance(app), kind="ghost", size=8,
        enabled=bool(loaders.installed_versions(app._data_dir())),
    )
    app.page_buttons.append(instance_btn)

    draw.rr(cv, CX0, 116, CX0 + 566, 152, 10, fill=theme.FIELD, outline=theme.BORDER, tags="page")
    app.entries.get("cat_query") or app._entry("cat_query", str(data["query"]))
    app._place_entry("cat_query", CX0, 116, 566, 36)
    entry = app.entries["cat_query"]
    entry.bind("<Return>", lambda _e: _search(app, reset=True))
    find = CanvasButton(app, CX0 + 578, 116, CX0 + 692, 152, "Искать",
                        command=lambda: _search(app, reset=True), kind="primary", size=8,
                        enabled=not data["busy"])
    app.page_buttons.append(find)

    category = catalog.category_label(data["category"]) if data["category"] else "Все категории"
    sort = catalog.sort_label(data["sort"])
    env = {"": "Везде", "client": "Клиент", "server": "Сервер"}[data["env"]]
    cat_btn = CanvasButton(app, CX0, 160, CX0 + 236, 190,
                           f"{i18n.tr('Категория')}: {i18n.tr(category)}",
                           command=lambda: _cycle(app, "category"), kind="ghost", size=8)
    sort_btn = CanvasButton(app, CX0 + 244, 160, CX0 + 452, 190,
                            f"{i18n.tr('Сорт')}: {i18n.tr(sort)}",
                            command=lambda: _cycle(app, "sort"), kind="ghost", size=8)
    env_btn = CanvasButton(app, CX0 + 460, 160, CX0 + 626, 190,
                           f"{i18n.tr('Среда')}: {i18n.tr(env)}",
                           command=lambda: _cycle(app, "env"), kind="ghost", size=8)
    reset_btn = CanvasButton(app, CX0 + 634, 160, CX0 + 748, 190, "Сброс",
                             command=lambda: _reset(app), kind="ghost", size=8)
    favorites = _favorites(app)
    fav_btn = CanvasButton(
        app, CX0 + 756, 160, CX1, 190,
        f"★ {len(favorites)}" if favorites else "★ Избранное",
        command=lambda: _toggle_favorites_filter(app), size=8,
        kind="primary" if data["favorites_only"] else "ghost",
    )
    app.page_buttons.extend([cat_btn, sort_btn, env_btn, reset_btn, fav_btn])

    if data["error"]:
        draw.text(cv, CX0, GRID_BOTTOM + 22, _short(data["error"], 110), 8, theme.DANGER,
                  "normal", "w", "page")
    elif data["busy"]:
        draw.text(cv, CX0, GRID_BOTTOM + 22, "Загружаю каталог…", 8, theme.MUTED,
                  "normal", "w", "page")
    elif data["loaded"]:
        draw.text(cv, CX0, GRID_BOTTOM + 22,
                  f"{i18n.tr('Найдено')}: {data['total']} · "
                  f"{i18n.tr('страница')} {data['page'] + 1} {i18n.tr('из')} {data['pages']}"
                  + (f" · {i18n.tr('офлайн-копия')}" if data.get("offline") else ""),
                  8, theme.WARNING if data.get("offline") else theme.MUTED,
                  "normal", "w", "page")
        if data["install_status"]:
            draw.text(cv, CX0 + 340, GRID_BOTTOM + 22, _short(data["install_status"], 76),
                      8, theme.ACCENT, "normal", "w", "page")
    else:
        draw.text(cv, CX0, GRID_BOTTOM + 22, "Введите запрос или листайте популярное.",
                  8, theme.DIM, "normal", "w", "page")

    items = data["items"]
    if data["favorites_only"]:
        favorites = set(_favorites(app))
        items = [item for item in items if item["id"] in favorites]
    if not items and data["loaded"] and not data["busy"]:
        draw.rr(cv, CX0, GRID_TOP, CX1, GRID_TOP + 96, 16, fill=theme.CARD,
                outline=theme.BORDER, tags="page")
        title = "В избранном пусто" if data["favorites_only"] else "Ничего не нашлось"
        hint = ("Отметьте моды звездой — они появятся здесь."
                if data["favorites_only"]
                else "Снимите фильтры кнопкой «Сброс» или уточните запрос.")
        draw.text(cv, CX0 + 26, GRID_TOP + 34, title, 12, theme.TEXT, "bold",
                  "w", "page")
        draw.text(cv, CX0 + 26, GRID_TOP + 62, hint, 8, theme.MUTED,
                  "normal", "w", "page")

    for index, item in enumerate(items[:COLS * ROWS]):
        col = index % COLS
        row = index // COLS
        x = CX0 + col * (CARD_W + GAP)
        y = GRID_TOP + row * (CARD_H + GAP)
        _draw_card(app, item, x, y, installable)

    if data["loaded"] and data["pages"] > 1:
        prev = CanvasButton(app, CX1 - 96, GRID_BOTTOM + 8, CX1 - 64, GRID_BOTTOM + 38, "←",
                            command=lambda: _go(app, -1), kind="ghost", size=9,
                            enabled=data["page"] > 0 and not data["busy"])
        nxt = CanvasButton(app, CX1 - 58, GRID_BOTTOM + 8, CX1 - 26, GRID_BOTTOM + 38, "→",
                           command=lambda: _go(app, 1), kind="ghost", size=9,
                           enabled=data["page"] + 1 < data["pages"] and not data["busy"])
        app.page_buttons.extend([prev, nxt])

    if not data["loaded"] and not data["busy"] and not data["error"]:
        app.after(60, lambda: _search(app, reset=True))


def _toggle_favorites_filter(app: "Launcher") -> None:
    data = state(app)
    data["favorites_only"] = not data["favorites_only"]
    app._show_page("mods")


def _draw_card(app: "Launcher", item: dict, x: int, y: int, installable: bool) -> None:
    cv = app.canvas
    tag = f"cat_card_{item['id'][:10]}"
    accent = _accent(item)
    frame = draw.rr(cv, x, y, x + CARD_W, y + CARD_H, 14, fill=theme.CARD,
                    outline=theme.BORDER, tags=("page", tag))
    cv.tag_bind(tag, "<Button-1>", lambda _e, pid=item["id"]: _open_detail(app, pid))
    cv.tag_bind(tag, "<Enter>", lambda _e: (cv.itemconfigure(frame, outline=accent),
                                            cv.configure(cursor="hand2")))
    cv.tag_bind(tag, "<Leave>", lambda _e: (cv.itemconfigure(frame, outline=theme.BORDER),
                                            cv.configure(cursor="")))

    star_tag = f"cat_fav_{item['id'][:10]}"
    favorite = _is_favorite(app, item["id"])
    draw.text(cv, x + CARD_W - 16, y + 16, "★" if favorite else "☆", 10,
              theme.ACCENT if favorite else theme.DIM, "normal", "center",
              ("page", star_tag))
    cv.tag_bind(star_tag, "<Button-1>",
                lambda _e, pid=item["id"]: _toggle_favorite(app, pid))
    cv.tag_bind(star_tag, "<Enter>", lambda _e: cv.configure(cursor="hand2"))
    cv.tag_bind(star_tag, "<Leave>", lambda _e: cv.configure(cursor=""))

    draw.cube(cv, x + 30, y + 30, 24, accent, tags=("page", tag))
    draw.text(cv, x + 52, y + 22, _short(item["title"], 24), 9, theme.TEXT, "bold", "w",
              ("page", tag))
    author = item.get("author") or "автор неизвестен"
    draw.text(cv, x + 52, y + 40, _short(author, 24), 7, theme.DIM, "normal", "w",
              ("page", tag))

    for offset, line in enumerate(_two_lines(item.get("description"))):
        draw.text(cv, x + 14, y + 62 + offset * 14, line, 8, theme.MUTED, "normal", "w",
                  ("page", tag))

    downloads = f"{int(item.get('downloads') or 0):,}".replace(",", " ")
    category = catalog.display_category(item)
    extra = f" · {i18n.tr(category)}" if category else ""
    draw.text(cv, x + 14, y + 96,
              f"{downloads} {i18n.tr('загрузок')}{extra}", 7, theme.DIM, "normal", "w",
              ("page", tag))

    installing = state(app)["installing"] == item["id"]
    label = "Устанавливаю…" if installing else "Установить"
    button = CanvasButton(app, x + 12, y + 106, x + CARD_W - 12, y + 138, label,
                          command=lambda it=item: _install(app, it), kind="primary", size=8,
                          enabled=installable and not state(app)["busy"])
    app.page_buttons.append(button)


def _cycle_instance(app: "Launcher") -> None:
    """Следующая установленная сборка — моды ставятся в неё."""
    rows = loaders.installed_versions(app._data_dir())
    if not rows:
        return
    ids = [str(row["id"]) for row in rows]
    current = app.installed_id
    index = (ids.index(current) + 1) % len(ids) if current in ids else 0
    row = rows[index]
    app.game = str(row["game"])
    app.loader = str(row["loader"])
    app.cfg["selected_version"] = app.game
    app.cfg["selected_loader"] = app.loader
    paths.save_config(app.cfg)
    app._show_page("mods")


def _switch_view(app: "Launcher", view: str) -> None:
    app.mods_view = view
    app._show_page("mods")


def _cycle(app: "Launcher", field: str) -> None:
    data = state(app)
    if field == "category":
        keys = [key for key, _label in catalog.CATEGORIES]
        data["category"] = keys[(keys.index(data["category"]) + 1) % len(keys)] \
            if data["category"] in keys else keys[1]
    elif field == "sort":
        keys = [key for key, _label in catalog.SORTS]
        data["sort"] = keys[(keys.index(data["sort"]) + 1) % len(keys)]
    elif field == "env":
        keys = [key for key, _label in catalog.ENVIRONMENTS]
        data["env"] = keys[(keys.index(data["env"]) + 1) % len(keys)]
    data["page"] = 0
    _search(app, reset=True)


def _reset(app: "Launcher") -> None:
    data = state(app)
    data.update(category="", sort="relevance", env="", page=0)
    entry = app.entries.get("cat_query")
    if entry is not None:
        entry.delete(0, "end")
    data["query"] = ""
    _search(app, reset=True)


def _go(app: "Launcher", delta: int) -> None:
    data = state(app)
    data["page"] = max(0, min(data["pages"] - 1, data["page"] + delta))
    _search(app, reset=False)


def _search(app: "Launcher", reset: bool) -> None:
    data = state(app)
    if data["busy"]:
        return
    entry = app.entries.get("cat_query")
    if entry is not None:
        data["query"] = entry.get().strip()
    if reset:
        data["page"] = 0
    data.update(busy=True, error="", install_status="")
    app._show_page("mods")

    query = data["query"]
    page = data["page"]
    category = data["category"]
    sort = data["sort"]
    env = data["env"]
    game, loader = app._instance_meta()

    def work() -> None:
        try:
            result = catalog.search(query, game, loader, category, env, sort, page)
            error = ""
        except modrinth.ModrinthError as exc:
            result = {"items": [], "total": 0, "pages": 1, "page": page}
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - показываем пользователю текст
            result = {"items": [], "total": 0, "pages": 1, "page": page}
            error = str(exc)
        data.update(items=result["items"], total=result["total"], pages=result["pages"],
                    page=result["page"], busy=False, loaded=True, error=error,
                    offline=bool(result.get("offline")))
        app._post(lambda: app._show_page("mods") if app.page == "mods" else None)

    threading.Thread(target=work, daemon=True, name="catalog-search").start()


def _open_detail(app: "Launcher", project_id: str) -> None:
    data = state(app)
    data.update(detail=None, detail_busy=True, detail_error="", modal_item=project_id)
    app.modal = "catalog"
    app._hide_entries()
    app._draw_modal()
    game, loader = app._instance_meta()

    def work() -> None:
        try:
            detail = catalog.details(project_id)
            versions = catalog.project_versions(project_id, game, loader)
            error = ""
        except modrinth.ModrinthError as exc:
            detail, versions, error = None, [], str(exc)
        except Exception as exc:  # noqa: BLE001
            detail, versions, error = None, [], str(exc)
        data.update(detail=detail, detail_versions=versions, detail_busy=False,
                    detail_error=error)
        app._post(lambda: app._draw_modal() if app.modal == "catalog" else None)

    threading.Thread(target=work, daemon=True, name="catalog-detail").start()


def draw_detail(app: "Launcher", px0, px1, py0, py1) -> None:
    cv = app.canvas
    data = state(app)
    detail = data.get("detail") or {}
    close = CanvasButton(app, px1 - 62, py0 + 20, px1 - 24, py0 + 52, "×",
                         command=app._close_modal, kind="ghost", size=12)
    app.modal_buttons.append(close)
    back_top = CanvasButton(app, px1 - 186, py0 + 20, px1 - 74, py0 + 52,
                            "← Каталог", command=app._close_modal,
                            kind="ghost", size=9)
    app.modal_buttons.append(back_top)

    if data["detail_busy"]:
        draw.text(cv, px0 + 28, py0 + 34, "Загрузка карточки…", 14, theme.TEXT, "bold", "w",
                  "modal")
        draw.text(cv, px0 + 28, py0 + 70, "Modrinth отвечает, это несколько секунд.", 8,
                  theme.MUTED, "normal", "w", "modal")
        back = CanvasButton(app, px0 + 28, py1 - 58, px0 + 208, py1 - 20,
                            "Назад в каталог", command=app._close_modal,
                            kind="ghost", size=9)
        app.modal_buttons.append(back)
        return
    if data["detail_error"] or not detail:
        draw.text(cv, px0 + 28, py0 + 34, "Не удалось открыть мод", 14, theme.TEXT, "bold",
                  "w", "modal")
        draw.text(cv, px0 + 28, py0 + 70, _short(data["detail_error"], 100), 8, theme.DANGER,
                  "normal", "w", "modal")
        back = CanvasButton(app, px0 + 28, py1 - 58, px0 + 208, py1 - 20,
                            "Назад в каталог", command=app._close_modal,
                            kind="ghost", size=9)
        app.modal_buttons.append(back)
        return

    title = _short(detail.get("title"), 44)
    draw.text(cv, px0 + 28, py0 + 32, title, 14, theme.TEXT, "bold", "w", "modal")
    meta = f"{int(detail.get('downloads') or 0):,} {i18n.tr('загрузок')}".replace(",", " ")
    if detail.get("license"):
        meta += f" · {i18n.tr('лицензия')} {_short(detail['license'], 34)}"
    draw.text(cv, px0 + 28, py0 + 56, meta, 8, theme.MUTED, "normal", "w", "modal")

    categories = ", ".join(
        i18n.tr(catalog.category_label(tag))
        for tag in (detail.get("categories") or [])[:4]
    )
    if categories:
        draw.text(cv, px0 + 28, py0 + 76, _short(categories, 90), 8, theme.DIM, "normal", "w",
                  "modal")

    body = " ".join(str(detail.get("body") or detail.get("description") or "").split())
    draw.text(cv, px0 + 28, py0 + 102, _short(body, 620), 8, theme.MUTED, "normal", "nw",
              "modal", width=px1 - px0 - 56)

    draw.text(cv, px0 + 28, py0 + 226, "Версии для выбранной сборки", 9, theme.TEXT, "bold",
              "w", "modal")
    versions = data.get("detail_versions") or []
    if not versions:
        draw.text(cv, px0 + 28, py0 + 250, "Совместимых версий не найдено.", 8, theme.DIM,
                  "normal", "w", "modal")
    for index, row in enumerate(versions[:3]):
        y = py0 + 248 + index * 34
        draw.rr(cv, px0 + 28, y, px1 - 28, y + 28, 8, fill=theme.CARD, outline=theme.BORDER,
                tags="modal")
        label = (f"{_short(row.get('version_number') or row.get('name') or '—', 22)}"
                 f" · {row.get('type') or 'release'}")
        draw.text(cv, px0 + 40, y + 14, label, 8, theme.TEXT, "normal", "w", "modal")
        games = ", ".join((row.get("game_versions") or [])[:3])
        draw.text(cv, px1 - 132, y + 14, _short(games, 24), 7, theme.DIM, "normal", "e",
                  "modal")

    _game, loader = app._instance_meta()
    installable = bool((app.installed_id and loader in ("fabric", "forge", "neoforge"))
                       or getattr(app, "loader_instance_id", ""))
    installing = data["installing"] == detail.get("id")
    install = CanvasButton(app, px0 + 28, py1 - 58, px0 + 208, py1 - 20,
                           "Устанавливаю…" if installing else "Установить",
                           command=lambda: _install(app, detail), kind="primary", size=9,
                           enabled=installable and not installing)
    open_page = CanvasButton(app, px0 + 220, py1 - 58, px0 + 392, py1 - 20, "На Modrinth",
                             command=lambda: _browse(detail), kind="ghost", size=9)
    close_bottom = CanvasButton(app, px0 + 404, py1 - 58, px1 - 24, py1 - 20,
                                "Назад в каталог", command=app._close_modal,
                                kind="ghost", size=9)
    app.modal_buttons.extend([install, open_page, close_bottom])

    if data["install_status"]:
        draw.text(cv, px0 + 28, py1 - 76, _short(data["install_status"], 80), 8,
                  theme.ACCENT, "normal", "w", "modal")


def _browse(detail: dict) -> None:
    url = str(detail.get("url") or "")
    if not url and detail.get("slug"):
        url = f"https://modrinth.com/mod/{detail['slug']}"
    if url:
        try:
            webbrowser.open(url)
        except OSError:
            pass


def _install(app: "Launcher", item: dict) -> None:
    data = state(app)
    if data["busy"] or not app.installed_id:
        return
    project_id = str(item.get("id") or "")
    if not project_id:
        return
    _game, loader = app._instance_meta()
    if loader not in ("fabric", "forge", "neoforge"):
        if not app._activate_loader_instance():
            data["install_status"] = "Нужна сборка Fabric, Forge или NeoForge"
            app._show_page("mods")
            return
        _game, loader = app._instance_meta()
    data.update(installing=project_id, install_status="Устанавливаю…")
    if app.modal == "catalog":
        app._draw_modal()
    else:
        app._show_page("mods")
    data_dir = app._data_dir()
    instance_id = app.installed_id
    title = str(item.get("title") or project_id)

    def work() -> None:
        try:
            plan = catalog.plan(project_id, _game, loader)
            app._push_log_threadsafe(f"Каталог: {title} + файлов {len(plan)}")
            result = modrinth.install(plan, mods_core.mods_dir(data_dir, instance_id),
                                      log=app._push_log_threadsafe)
            status = f"{title}: файлов {len(result['installed']) + len(result['skipped'])}"
        except modrinth.ModrinthError as exc:
            status = f"{title}: {exc}"
        except Exception as exc:  # noqa: BLE001
            status = f"{title}: {exc}"
        data.update(installing="", install_status=_short(status, 90))
        app._post(lambda: app._draw_modal() if app.modal == "catalog"
                  else (app._show_page("mods") if app.page == "mods" else None))

    threading.Thread(target=work, daemon=True, name="catalog-install").start()
