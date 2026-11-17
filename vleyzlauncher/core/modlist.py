"""Экспорт списка установленных модов: txt для чтения, json для обмена.

Файл помогает показать сборку другу или вспомнить, что стоит, без
открывания папок. Ничего не скачивает и не меняет.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import loaders, mods as mods_core, paths


def _meta(data_dir: Path, instance_id: str) -> dict:
    for row in loaders.installed_versions(data_dir):
        if row["id"] == instance_id:
            return row
    return {}


def collect(data_dir: Path, instance_id: str) -> dict:
    data_dir = Path(data_dir)
    row = _meta(data_dir, instance_id)
    items = mods_core.list_mods(data_dir, instance_id)
    return {
        "instance": instance_id,
        "game": str(row.get("game") or ""),
        "loader": str(row.get("loader") or ""),
        "total": len(items),
        "enabled": sum(1 for item in items if item.get("enabled")),
        "mods": [
            {
                "name": str(item.get("name") or ""),
                "size": int(item.get("size") or 0),
                "enabled": bool(item.get("enabled")),
            }
            for item in items
        ],
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def render_text(data: dict) -> str:
    lines = [
        "# Vleyz Launcher · список модов",
        f"# Сборка: {data['instance']} · Minecraft {data['game']} · {data['loader']}",
        f"# Всего: {data['total']}, включено: {data['enabled']}",
        f"# Создано: {data['generated']}",
        "",
    ]
    for index, item in enumerate(data["mods"], start=1):
        state = "вкл" if item["enabled"] else "выкл"
        lines.append(f"{index:>3}. {item['name']} · {paths.human_size(item['size'])} · {state}")
    if not data["mods"]:
        lines.append("Модов нет.")
    return "\n".join(lines) + "\n"


def export_text(data_dir: Path, instance_id: str, target: Path) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_text(collect(data_dir, instance_id)), encoding="utf-8")
    return target


def export_json(data_dir: Path, instance_id: str, target: Path) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(collect(data_dir, instance_id), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target
