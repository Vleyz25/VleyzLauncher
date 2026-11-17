"""Плагины-пресеты: JSON-файлы, которые добавляют готовые сборки.

Код плагинов не исполняется — только данные. Файл кладётся в папку
`plugins` рядом с конфигом, формат:

    {"name": "Мои сборки", "version": 1,
     "presets": [{"id": "skyblock", "title": "SkyBlock",
                  "categories": ["adventure", "utility"], "limit": 14}]}

Такой пресет появляется в генераторе рядом со встроенными.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from . import catalog, paths

log = logging.getLogger(__name__)

MAX_PRESETS = 50
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
_ALLOWED = {slug for slug, _label in catalog.CATEGORIES if slug}


class PluginError(ValueError):
    pass


def plugins_dir() -> Path:
    return paths.state_dir() / "plugins"


def ensure_dir() -> Path:
    folder = plugins_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _clean_preset(raw: object, plugin_name: str) -> dict | None:
    if not isinstance(raw, dict):
        return None
    preset_id = str(raw.get("id") or "").strip().lower()
    if not _ID.match(preset_id):
        log.warning("плагин %s: пропущен пресет с id %r", plugin_name, preset_id)
        return None
    title = " ".join(str(raw.get("title") or "").split())[:48]
    if not title:
        return None
    categories = [
        str(item) for item in (raw.get("categories") or []) if str(item) in _ALLOWED
    ]
    if not categories:
        return None
    try:
        limit = int(raw.get("limit") or 15)
    except (TypeError, ValueError):
        limit = 15
    return {
        "id": f"{plugin_name}:{preset_id}" if plugin_name else preset_id,
        "title": title,
        "categories": categories[:4],
        "limit": max(5, min(40, limit)),
        "plugin": plugin_name,
    }


def load() -> list[dict]:
    """Все валидные пресеты из папки плагинов. Битые файлы пропускаются."""
    folder = plugins_dir()
    if not folder.is_dir():
        return []
    presets: list[dict] = []
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("плагин %s не прочитан: %s", path.name, exc)
            continue
        if not isinstance(data, dict):
            continue
        name = " ".join(str(data.get("name") or path.stem).split())[:40] or path.stem
        items = data.get("presets")
        if not isinstance(items, list):
            continue
        for raw in items[:MAX_PRESETS]:
            preset = _clean_preset(raw, name)
            if preset:
                presets.append(preset)
    return presets[:MAX_PRESETS]


EXAMPLE = {
    "name": "Мои сборки",
    "version": 1,
    "presets": [
        {
            "id": "skyblock",
            "title": "SkyBlock",
            "categories": ["adventure", "utility"],
            "limit": 14,
        },
        {
            "id": "redstone",
            "title": "Редстоун",
            "categories": ["technology", "utility"],
            "limit": 12,
        },
    ],
}


def write_example(overwrite: bool = False) -> Path:
    """Кладёт пример плагина, чтобы человек увидел формат."""
    ensure_dir()
    path = plugins_dir() / "example.json"
    if path.exists() and not overwrite:
        return path
    path.write_text(json.dumps(EXAMPLE, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
