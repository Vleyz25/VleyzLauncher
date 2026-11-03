from __future__ import annotations

import uuid

DEFAULT_NICKNAME = "Steve"


def profiles(cfg: dict) -> list[dict]:
    items = cfg.get("profiles")
    if not isinstance(items, list):
        items = []
        cfg["profiles"] = items
    return items


def ensure(cfg: dict) -> list[dict]:
    """Гарантирует хотя бы один профиль и корректный активный."""
    items = profiles(cfg)
    if not items:
        items.append({
            "id": uuid.uuid4().hex[:8],
            "name": "Основной",
            "nickname": (cfg.get("nickname") or DEFAULT_NICKNAME)[:16],
        })
    active_id = cfg.get("active_profile")
    if not active_id or not any(item.get("id") == active_id for item in items):
        cfg["active_profile"] = items[0]["id"]
    current = active(cfg)
    if current.get("nickname"):
        cfg["nickname"] = current["nickname"]
    return items


def active(cfg: dict) -> dict:
    items = profiles(cfg)
    active_id = cfg.get("active_profile")
    for item in items:
        if item.get("id") == active_id:
            return item
    return items[0]


def create(cfg: dict, name: str, nickname: str) -> dict:
    profile = {
        "id": uuid.uuid4().hex[:8],
        "name": (name or "Профиль").strip()[:24] or "Профиль",
        "nickname": (nickname or DEFAULT_NICKNAME).strip()[:16] or DEFAULT_NICKNAME,
    }
    profiles(cfg).append(profile)
    return profile


def activate(cfg: dict, profile_id: str) -> bool:
    for item in profiles(cfg):
        if item.get("id") == profile_id:
            cfg["active_profile"] = profile_id
            cfg["nickname"] = item.get("nickname") or DEFAULT_NICKNAME
            return True
    return False


def update(cfg: dict, profile_id: str, name: str | None = None, nickname: str | None = None) -> bool:
    for item in profiles(cfg):
        if item.get("id") != profile_id:
            continue
        if name is not None:
            item["name"] = name.strip()[:24] or item.get("name", "Профиль")
        if nickname is not None:
            item["nickname"] = nickname.strip()[:16] or DEFAULT_NICKNAME
        if cfg.get("active_profile") == profile_id:
            cfg["nickname"] = item["nickname"]
        return True
    return False


def remove(cfg: dict, profile_id: str) -> bool:
    items = profiles(cfg)
    if len(items) <= 1:
        return False
    remaining = [item for item in items if item.get("id") != profile_id]
    if len(remaining) == len(items):
        return False
    cfg["profiles"] = remaining
    if cfg.get("active_profile") == profile_id:
        cfg["active_profile"] = remaining[0]["id"]
        cfg["nickname"] = remaining[0].get("nickname") or DEFAULT_NICKNAME
    return True
