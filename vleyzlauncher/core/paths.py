from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_FOLDER = "VleyzLauncher"
DEFAULT_NICKNAME = "Steve"


def app_dir() -> Path:
    """Папка рядом с исполняемым файлом (или с исходниками при запуске из Python)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def default_data_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_FOLDER / "minecraft"
    return Path.home() / f".{APP_FOLDER.lower()}"


def java_root() -> Path:
    """Куда лаунчер ставит скачанные JRE."""
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_FOLDER / "java"
    return Path.home() / f".{APP_FOLDER.lower()}" / "java"


CONFIG_DEFAULTS = {
    "nickname": DEFAULT_NICKNAME,
    "ram_mb": 2048,
    "data_dir": "",
    "java_path": "",
    "selected_version": "",
    "selected_loader": "vanilla",
    "theme": "dark",
    "profiles": [],
    "active_profile": "",
    "ely_auth": {},
    "ely_enabled": False,
    "microsoft_auth": {},
    "account_mode": "offline",
    "servers": [],
}


def config_path() -> Path:
    return app_dir() / "config.json"


def load_config() -> dict:
    cfg = dict(CONFIG_DEFAULTS)
    path = config_path()
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                cfg.update({k: v for k, v in stored.items() if k in CONFIG_DEFAULTS})
        except (OSError, ValueError):
            pass
    if not cfg.get("data_dir"):
        cfg["data_dir"] = str(default_data_dir())
    return cfg


def save_config(cfg: dict) -> None:
    try:
        config_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def data_dir(cfg: dict) -> Path:
    return Path(cfg.get("data_dir") or default_data_dir())


def human_size(num: float) -> str:
    num = float(max(0, num))
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if num < 1024 or unit == "ГБ":
            if unit == "Б":
                return f"{int(num)} {unit}"
            return f"{num:.1f} {unit}".replace(".", ",")
        num /= 1024
    return f"{num:.1f} ГБ"


def human_speed(bps: float) -> str:
    if bps <= 0:
        return "—"
    return human_size(bps) + "/с"
