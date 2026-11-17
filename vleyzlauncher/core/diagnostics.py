"""Диагностика: снимок системы, проверки и архив с логами.

Нужна, когда «не запускается» и непонятно почему. Отчёт не содержит
секретов: пароли, токены и клиентский секрет GigaChat вырезаются.
"""

from __future__ import annotations

import json
import logging
import platform
import shutil
import sys
import time
import zipfile
from pathlib import Path

from .. import APP_NAME, APP_VERSION
from . import loaders, modmeta, mods as mods_core, paths

log = logging.getLogger(__name__)

SECRET_SECTIONS = ("ely_auth", "microsoft_auth")


def _public_config(cfg: dict) -> dict:
    result = {
        key: value for key, value in cfg.items()
        if key not in SECRET_SECTIONS and key != "gigachat"
    }
    block = cfg.get("gigachat")
    if isinstance(block, dict):
        result["gigachat"] = {
            "enabled": bool(block.get("enabled")),
            "model": str(block.get("model") or ""),
            "has_keys": bool(block.get("client_id") and block.get("client_secret")),
        }
    return result


def _free_gb(path: Path) -> float:
    try:
        return round(shutil.disk_usage(path).free / (1024 ** 3), 1)
    except OSError:
        return 0.0


def snapshot(cfg: dict) -> dict:
    data = paths.data_dir(cfg)
    versions = loaders.installed_versions(data)
    return {
        "app": {
            "name": APP_NAME,
            "version": APP_VERSION,
            "frozen": bool(getattr(sys, "frozen", False)),
        },
        "system": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "paths": {
            "app": str(paths.app_dir()),
            "state": str(paths.state_dir()),
            "data": str(data),
            "portable": paths.is_portable(),
        },
        "config": _public_config(cfg),
        "instances": [
            {"id": row["id"], "game": row["game"], "loader": row["loader"]}
            for row in versions
        ],
        "disk": {"free_gb": _free_gb(data if data.is_dir() else paths.state_dir())},
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def instance_issues(cfg: dict, instance_id: str,
                    items: list[dict] | None = None) -> list[str]:
    """Проверки одной сборки: дубли модов и моды на vanilla."""
    data = paths.data_dir(cfg)
    row = next((item for item in loaders.installed_versions(data)
                if item["id"] == instance_id), None)
    if row is None:
        return ["Сборка не найдена"]
    if items is None:
        try:
            items = mods_core.list_mods(data, instance_id)
        except OSError:
            return [f"Папка модов сборки {instance_id} недоступна"]
    issues: list[str] = []
    seen: set[str] = set()
    for item in items:
        name = str(item.get("name") or "").lower()
        if name in seen:
            issues.append(f"Дублируется мод {item.get('name')} в {instance_id}")
        seen.add(name)
    if row.get("loader") == "vanilla" and items:
        issues.append(f"В сборке {instance_id} моды, но загрузчик vanilla")
    records = modmeta.scan(mods_core.mods_dir(data, instance_id))
    for group in modmeta.duplicates(records):
        issues.append(f"Дублируется мод {group['id']}: {', '.join(group['files'])}")
    for missing in modmeta.missing_dependencies(records)[:5]:
        issues.append(f"{missing['mod']} требует {missing['dep']}")
    return issues


def health(cfg: dict) -> list[str]:
    """Простые проверки, которые чаще всего объясняют проблему."""
    data = paths.data_dir(cfg)
    versions = loaders.installed_versions(data)
    if not versions:
        return ["Нет установленных версий игры"]
    issues: list[str] = []
    if _free_gb(data if data.is_dir() else paths.state_dir()) < 1.0:
        issues.append("На диске меньше 1 ГБ свободного места")
    for row in versions:
        issues.extend(instance_issues(cfg, row["id"]))
    return issues


def _recent_logs(limit: int = 3) -> list[Path]:
    folder = paths.logs_dir()
    if not folder.is_dir():
        return []
    files = sorted(folder.glob("*.log"), key=lambda item: item.stat().st_mtime,
                   reverse=True)
    return files[:limit]


def export(cfg: dict, target: Path | None = None) -> Path:
    """Собирает zip с report.json и последними логами."""
    report = snapshot(cfg)
    report["health"] = health(cfg)
    if target is None:
        folder = paths.state_dir() / "diagnostics"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"report-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report.json",
                         json.dumps(report, ensure_ascii=False, indent=2))
        for log_file in _recent_logs():
            try:
                archive.write(log_file, f"logs/{log_file.name}")
            except OSError:
                continue
    return target
