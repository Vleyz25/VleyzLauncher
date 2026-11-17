"""Страница «Сборщик»: запрос словами → план модов → установка.

Пресеты дают быстрый старт без ввода текста, плагины добавляют свои.
Если настроен GigaChat, он уточняет намерение; при любой ошибке разбор
откатывается на правила, и страница остаётся рабочей.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

from ... import i18n
from ...core import builder, gigachat, mods as mods_core, plugins
from .. import draw, theme
from ..layout import CX0, CX1
from ..widgets import CanvasButton

if TYPE_CHECKING:
    from ..app import Launcher

MAX_ROWS = 8
ROW_H = 40
LIMITS = (10, 15, 20, 30)


def state(app: "Launcher") -> dict:
    data = getattr(app, "_builder_state", None)
    if data is None:
        data = {
            "query": "",
            "limit": 15,
            "skip": 0,
            "busy": False,
            "installing": False,
            "items": [],
            "notes": [],
            "title": "",
            "intent": None,
            "error": "",
            "status": "",
            "loaded": False,
        }
        app._builder_state = data
    return data


def presets() -> list[dict]:
    rows = [dict(item) for item in builder.PRESETS]
    try:
        rows.extend(plugins.load())
    except OSError:
        pass
    return rows


def _short(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def draw_page(app: "Launcher") -> None:
    cv = app.canvas
    data = state(app)
    game, loader = app._instance_meta()
    installable = bool(app.installed_id and loader in builder.SUPPORTED_LOADERS)
    subtitle = (
        f"{game} · {loader} · подберу моды под запрос"
        if installable
        else "Выберите сборку на «Играть» — установка пойдёт в её папку mods"
    )
    app._header("Сборщик сборок", subtitle)

    draw.rr(cv, CX0, 116, CX0 + 566, 152, 10, fill=theme.FIELD, outline=theme.BORDER,
            tags="page")
    app.entries.get("bld_query") or app._entry("bld_query", str(data["query"]))
    app._place_entry("bld_query", CX0, 116, 566, 36)
    entry = app.entries["bld_query"]
    entry.bind("<Return>", lambda _e: _generate(app))
    build = CanvasButton(app, CX0 + 578, 116, CX0 + 700, 152, "Собрать",
                         command=lambda: _generate(app), kind="primary", size=9,
                         enabled=not data["busy"])
    clear = CanvasButton(app, CX0 + 708, 116, CX0 + 844, 152, "Очистить",
                         command=lambda: _reset(app), kind="ghost", size=9)
    app.page_buttons.extend([build, clear])

    items = presets()[:12]
    for index, preset in enumerate(items):
        row = index // 6
        col = index % 6
        x = CX0 + col * 144
        y = 162 + row * 38
        kind = "primary" if preset.get("title") == data["title"] else "ghost"
        button = CanvasButton(app, x, y, x + 138, y + 32, _short(preset["title"], 18),
                              command=lambda p=preset: _generate_preset(app, p),
                              kind=kind, size=8, enabled=not data["busy"])
        app.page_buttons.append(button)

    status_y = 246
    if data["error"]:
        draw.text(cv, CX0, status_y, _short(data["error"], 84), 8, theme.DANGER,
                  "normal", "w", "page")
    elif data["busy"]:
        draw.text(cv, CX0, status_y, "Подбираю моды…", 8, theme.MUTED, "normal", "w", "page")
    elif data["status"]:
        text = data["status"]
        if data["notes"]:
            text += " · " + data["notes"][0]
        draw.text(cv, CX0, status_y, _short(text, 84), 8, theme.ACCENT,
                  "normal", "w", "page")
    else:
        draw.text(cv, CX0, status_y,
                  "Опишите сборку словами или выберите пресет — моды возьмутся из Modrinth.",
                  8, theme.DIM, "normal", "w", "page")

    limit_btn = CanvasButton(app, CX1 - 150, 236, CX1, 262,
                             f"{i18n.tr('Модов')}: {data['limit']}",
                             command=lambda: _cycle_limit(app), kind="ghost", size=8,
                             enabled=not data["busy"])
    app.page_buttons.append(limit_btn)

    panel_top = 264
    panel_bottom = 600
    draw.rr(cv, CX0, panel_top, CX1, panel_bottom, 16, fill=theme.CARD,
            outline=theme.BORDER, tags="page")

    plan = data["items"]
    if not plan:
        draw.text(cv, CX0 + 28, panel_top + 40, "Плана пока нет", 12, theme.TEXT, "bold",
                  "w", "page")
        draw.text(cv, CX0 + 28, panel_top + 66,
                  "Соберите план — здесь появятся моды, которые лаунчер поставит вместе "
                  "с зависимостями.", 8, theme.MUTED, "normal", "w", "page", width=780)
    y = panel_top + 8
    for index, item in enumerate(plan[:MAX_ROWS]):
        accent = theme.ACCENT
        draw.rr(cv, CX0 + 12, y, CX1 - 12, y + ROW_H - 6, 10, fill=theme.CARD_BOTTOM,
                outline=theme.BORDER, tags="page")
        draw.cube(cv, CX0 + 34, y + 17, 18, accent, tags="page")
        draw.text(cv, CX0 + 54, y + 9, _short(item.get("title"), 44), 9, theme.TEXT,
                  "bold", "w", "page")
        reason = str(item.get("reason") or "")
        downloads = f"{int(item.get('downloads') or 0):,}".replace(",", " ")
        draw.text(cv, CX0 + 54, y + 24,
                  f"{_short(reason, 20)} · {downloads} {i18n.tr('загрузок')}", 7,
                  theme.DIM, "normal", "w", "page")
        y += ROW_H

    if len(plan) > MAX_ROWS:
        draw.text(cv, CX1 - 24, panel_bottom - 16,
                  f"+{len(plan) - MAX_ROWS} {i18n.tr('ещё')}", 8, theme.MUTED, "normal", "e",
                  "page")

    install = CanvasButton(app, CX0, 616, CX0 + 224, 660,
                           "Устанавливаю…" if data["installing"] else "Установить всё",
                           command=lambda: _install(app), kind="primary", size=9,
                           enabled=bool(plan) and installable and not data["busy"]
                           and not data["installing"])
    open_folder = CanvasButton(app, CX0 + 236, 616, CX0 + 456, 660, "Папка mods",
                               command=lambda: app._add_mods(), kind="ghost", size=9,
                               enabled=bool(app.installed_id))
    plugins_btn = CanvasButton(app, CX1 - 236, 616, CX1 - 24, 660, "Плагины",
                               command=lambda: _open_plugins(app), kind="ghost", size=9)
    more = CanvasButton(app, CX0 + 456, 616, CX0 + 628, 660, "Ещё вариант",
                        command=lambda: _more(app), kind="ghost", size=9,
                        enabled=bool(plan) and not data["busy"] and not data["installing"])
    app.page_buttons.extend([install, open_folder, more, plugins_btn])

    if not installable:
        draw.text(cv, CX0 + 470, 604, "Нужна сборка с загрузчиком.", 8,
                  theme.DIM, "normal", "w", "page")


def _open_plugins(app: "Launcher") -> None:
    """Папка плагинов и пример формата — чтобы было видно, что класть."""
    try:
        folder = plugins.ensure_dir()
        plugins.write_example()
        os.startfile(str(folder))  # noqa: S606 - открытие проводника
        app._push_log(f"Плагины: {folder}")
    except OSError as exc:
        app._push_log(f"Папка плагинов не открылась: {exc}")


def _reset(app: "Launcher") -> None:
    data = state(app)
    data.update(query="", items=[], notes=[], title="", intent=None, error="",
                status="", loaded=False)
    entry = app.entries.get("bld_query")
    if entry is not None:
        entry.delete(0, "end")
    app._show_page("builder")


def _generate_preset(app: "Launcher", preset: dict) -> None:
    try:
        intent = builder.intent_from_preset(preset)
    except builder.BuilderError as exc:
        data = state(app)
        data["error"] = str(exc)
        app._show_page("builder")
        return
    _generate(app, intent=intent)


def _generate(app: "Launcher", intent: dict | None = None,
              skip: int | None = None) -> None:
    data = state(app)
    if data["busy"]:
        return
    entry = app.entries.get("bld_query")
    if entry is not None:
        data["query"] = entry.get().strip()
    game, loader = app._instance_meta()
    data["skip"] = 0 if skip is None else max(0, int(skip))
    data.update(busy=True, error="", status="Подбираю моды…", items=[], notes=[])
    app._show_page("builder")

    query = data["query"]
    limit = data["limit"]
    offset = data["skip"]
    use_ai = gigachat.enabled(app.cfg)
    cfg = dict(app.cfg)

    def work() -> None:
        notes: list[str] = []
        chosen = None
        try:
            if intent is None:
                chosen = builder.parse_intent(query)
            else:
                chosen = dict(intent)
            client = gigachat.from_config(cfg) if use_ai and intent is None else None
            if client is not None:
                try:
                    chosen = builder.parse_with_gigachat(client, query, game, loader)
                except Exception as exc:  # noqa: BLE001 - откат на правила
                    notes.append(f"GigaChat: {exc}")
            chosen["limit"] = limit
            items = builder.select(chosen, game, loader, offset)
            if chosen.get("shaders"):
                notes.append("Шейдеры ставятся отдельно: вкладка «Моды» → «Паки».")
            error = ""
            status = (f"{len(items)} модов · {chosen.get('title') or 'Сборка'}"
                      if items else "Ничего не нашлось")
            if chosen.get("source") == "rules" and chosen.get("matched") is False:
                status = ("Запрос не распознан — Vanilla+. "
                          "Уточните: магия, техно, приключения")
        except builder.BuilderError as exc:
            items, status, error = [], "", str(exc)
        except Exception as exc:  # noqa: BLE001 - показываем причину
            items, status, error = [], "", str(exc)
        data.update(items=items, intent=chosen, notes=notes, busy=False,
                    loaded=True, status=status, error=error,
                    title=str((chosen or {}).get("title") or ""))
        app._post(lambda: app._show_page("builder") if app.page == "builder" else None)

    threading.Thread(target=work, daemon=True, name="builder-plan").start()


def _cycle_limit(app: "Launcher") -> None:
    data = state(app)
    current = data["limit"] if data["limit"] in LIMITS else LIMITS[1]
    data["limit"] = LIMITS[(LIMITS.index(current) + 1) % len(LIMITS)]
    if data["intent"]:
        _generate(app, intent=data["intent"], skip=0)
    else:
        app._show_page("builder")


def _more(app: "Launcher") -> None:
    data = state(app)
    if not data["items"] or data["busy"]:
        return
    intent = data["intent"] or builder.parse_intent(data["query"])
    _generate(app, intent=intent, skip=data["skip"] + 6)


def _install(app: "Launcher") -> None:
    data = state(app)
    if data["busy"] or data["installing"] or not data["items"]:
        return
    if not app.installed_id:
        data["error"] = "Сначала выберите установленную сборку"
        app._show_page("builder")
        return
    game, loader = app._instance_meta()
    if loader not in builder.SUPPORTED_LOADERS:
        data["error"] = "Нужна сборка Fabric, Forge или NeoForge"
        app._show_page("builder")
        return
    data.update(installing=True, error="", status="Устанавливаю…")
    app._show_page("builder")
    instance_id = app.installed_id
    data_dir = app._data_dir()
    items = list(data["items"])

    def work() -> None:
        try:
            destination = mods_core.mods_dir(data_dir, instance_id)
            backup = builder.backup_mods(destination, data_dir / "backups" / "mods")
            if backup is not None:
                app._push_log_threadsafe(f"Резервная копия модов: {backup.name}")
            result = builder.install(items, destination, game, loader,
                                     log=app._push_log_threadsafe)
            total = len(result["installed"]) + len(result["skipped"])
            status = f"Готово: файлов {total}, скачано {len(result['installed'])}"
        except Exception as exc:  # noqa: BLE001 - показываем причину
            status = f"Ошибка установки: {exc}"
        data.update(installing=False, status=status)
        app._post(lambda: app._show_page("builder") if app.page == "builder" else None)

    threading.Thread(target=work, daemon=True, name="builder-install").start()
