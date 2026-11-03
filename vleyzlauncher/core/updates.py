"""Проверка обновлений по последнему релизу GitHub."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .. import APP_VERSION
from . import net

log = logging.getLogger(__name__)

REPO = "Vleyz25/VleyzLauncher"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE_URL = f"https://github.com/{REPO}/releases/latest"


def _parts(value: str) -> tuple[int, ...]:
    return tuple(int(item) for item in re.findall(r"\d+", str(value))[:3])


def is_newer(latest: str, current: str = APP_VERSION) -> bool:
    """Сравнить версии по числам: 1.10 новее 1.6, строка 'v' не мешает."""
    left = _parts(latest)
    right = _parts(current)
    if not left:
        return False
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return left > right


def check(current: str = APP_VERSION) -> dict | None:
    """Вернуть описание нового релиза или None, если обновляться не нужно."""
    try:
        data = net.fetch_json(API_URL, timeout=8, retries=1)
    except net.DownloadError as exc:
        log.info("проверка обновлений не удалась: %s", exc)
        return None
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "").strip()
    if not is_newer(tag, current):
        return None
    setup = None
    executables = []
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        if not name.lower().endswith(".exe"):
            continue
        executables.append(asset)
        if "setup" in name.lower():
            setup = asset
            break
    if setup is None and executables:
        setup = executables[0]
    return {
        "version": tag.lstrip("vV") or tag,
        "tag": tag,
        "url": str(data.get("html_url") or PAGE_URL),
        "setup_url": str((setup or {}).get("browser_download_url") or ""),
        "setup_size": int((setup or {}).get("size") or 0),
        "digest": str((setup or {}).get("digest") or ""),
        "notes": str(data.get("body") or "").strip()[:700],
        "published_at": str(data.get("published_at") or ""),
    }


def download_url(info: dict) -> str:
    return str(info.get("setup_url") or info.get("url") or PAGE_URL)


def download_update(info: dict, dest: Path, on_bytes=None, cancel=None) -> Path:
    """Скачать установщик обновления и проверить его размер и SHA256."""
    url = str(info.get("setup_url") or "")
    if not url:
        raise net.DownloadError("в последнем релизе нет установщика")
    dest = Path(dest)
    net.download(
        url,
        dest,
        size=int(info.get("setup_size") or 0) or None,
        on_bytes=on_bytes,
        cancel=cancel,
    )
    digest = str(info.get("digest") or "")
    if digest.startswith("sha256:"):
        if net.sha256_file(dest) != digest.split(":", 1)[1].lower():
            dest.unlink(missing_ok=True)
            raise net.DownloadError("SHA256 установщика не совпал")
    return dest


def install_command(setup: Path, target: Path) -> list[str]:
    """Команда тихой установки поверх текущей папки с перезапуском.

    cmd ждёт пару секунд, чтобы лаунчер успел закрыться и отпустил exe.
    """
    inner = (
        f'timeout /t 2 /nobreak >nul & "{setup}" '
        f'--silent --dir "{target}" --no-shortcuts --run'
    )
    return ["cmd", "/c", inner]
