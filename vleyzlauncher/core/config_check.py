"""Проверка и починка конфига: типы, диапазоны, мусор в списках.

Конфиг правит не только лаунчер, но и человек (и старые версии). Поэтому
перед использованием значения приводятся к безопасным: ник без управляющих
символов, память в разумных пределах, избранное без дублей, серверы и
профили — только словарями с нужными полями.
"""

from __future__ import annotations

from .. import i18n

NICKNAME_MAX = 16
RAM_MIN, RAM_MAX = 512, 32768
THEMES = ("dark", "grey", "light")
TEXT_LIMITS = {
    "data_dir": 260,
    "java_path": 260,
    "selected_version": 64,
    "selected_loader": 32,
    "jvm_args": 1024,
    "active_profile": 64,
}
MAX_FAVORITES = 200
MAX_SERVERS = 100
MAX_PROFILES = 50


def _text(value: object, limit: int) -> str:
    raw = "".join(ch for ch in str(value or "") if ch.isprintable())
    return raw.strip()[:limit]


def sanitize_nickname(value: object) -> str:
    name = _text(value, NICKNAME_MAX)
    return name or "Steve"


def _dicts(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def validate(cfg: dict) -> tuple[dict, list[str]]:
    """Возвращает исправленную копию и список того, что пришлось менять."""
    clean = dict(cfg)
    notes: list[str] = []

    nickname = sanitize_nickname(clean.get("nickname"))
    if nickname != clean.get("nickname"):
        clean["nickname"] = nickname
        notes.append("nickname")

    try:
        ram = int(clean.get("ram_mb"))
    except (TypeError, ValueError):
        ram = 2048
    ram = max(RAM_MIN, min(RAM_MAX, ram))
    if ram != clean.get("ram_mb"):
        clean["ram_mb"] = ram
        notes.append("ram_mb")

    if clean.get("theme") not in THEMES:
        clean["theme"] = "dark"
        notes.append("theme")
    if clean.get("language") not in i18n.LANGUAGES:
        clean["language"] = "ru"
        notes.append("language")

    for key, limit in TEXT_LIMITS.items():
        current = clean.get(key)
        value = _text(current, limit)
        if current is None:
            clean[key] = value
        elif value != current:
            clean[key] = value
            notes.append(key)

    favorites = []
    for item in clean.get("favorites") or []:
        if not isinstance(item, str):
            continue
        marker = _text(item, 64)
        if marker and marker not in favorites:
            favorites.append(marker)
    favorites = favorites[:MAX_FAVORITES]
    if favorites != clean.get("favorites"):
        clean["favorites"] = favorites
        notes.append("favorites")

    servers = []
    for item in _dicts(clean.get("servers"))[:MAX_SERVERS]:
        address = _text(item.get("address"), 128)
        if not address:
            continue
        entry = dict(item)
        entry["address"] = address
        entry["name"] = _text(item.get("name"), 48) or address
        servers.append(entry)
    if servers != clean.get("servers"):
        clean["servers"] = servers
        notes.append("servers")

    profiles = []
    for index, item in enumerate(_dicts(clean.get("profiles"))[:MAX_PROFILES]):
        entry = dict(item)
        entry["id"] = _text(item.get("id"), 32) or f"p{index + 1}"
        entry["name"] = _text(item.get("name"), 32) or entry["id"]
        entry["nickname"] = sanitize_nickname(item.get("nickname"))
        profiles.append(entry)
    if profiles != clean.get("profiles"):
        clean["profiles"] = profiles
        notes.append("profiles")

    if clean.get("active_profile") and not any(
        item["id"] == clean.get("active_profile") for item in profiles
    ):
        clean["active_profile"] = ""
        notes.append("active_profile")

    block = clean.get("gigachat")
    if isinstance(block, dict):
        fixed = dict(block)
        fixed["client_id"] = _text(block.get("client_id"), 128)
        fixed["client_secret"] = _text(block.get("client_secret"), 256)
        fixed["model"] = _text(block.get("model"), 48) or "GigaChat"
        fixed["enabled"] = bool(block.get("enabled"))
        if fixed != block:
            clean["gigachat"] = fixed
            notes.append("gigachat")

    return clean, notes
