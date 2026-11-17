from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

from . import net, paths, safepath

CREATE_NO_WINDOW = 0x08000000
ADOPTIUM = "https://api.adoptium.net/v3/assets/latest/{major}/hotspot"
ADOPTIUM_QUERY = "?architecture=x64&image_type=jre&os=windows&vendor=eclipse"
JAVA_MIN = 8
JAVA_MAX = 99  # верхней границы нет: нужную версию выбирает игра

_cache: dict[tuple[int, str], tuple[Path, int] | None] = {}
log = logging.getLogger(__name__)


def windows_version() -> tuple[int, int, int]:
    """Версия Windows (major, minor, build); на других ОС — нули."""
    if os.name != "nt" or not hasattr(sys, "getwindowsversion"):
        return (0, 0, 0)
    info = sys.getwindowsversion()
    return (int(info.major), int(info.minor), int(info.build))


def is_windows7() -> bool:
    """Windows 7 / Server 2008 R2: 6.1."""
    major, minor, _ = windows_version()
    return (major, minor) == (6, 1)


def max_java_major() -> int:
    """Старшая Java, которую официально поддерживает эта система.

    Adoptium собирает JRE 17+ только под Windows 10 и новее, на Windows 7
    работает лишь Java 8 — иначе игра падает ещё до окна.
    """
    if is_windows7():
        return 8
    return JAVA_MAX


def check_java_supported(needed: int) -> None:
    """Проверяет, что нужная Java вообще существует для этой Windows."""
    needed = int(needed or JAVA_MIN)
    limit = max_java_major()
    if needed > limit:
        raise net.DownloadError(
            f"Эта версия игры требует Java {needed}, а под Windows 7 выпускается только Java 8. "
            "Возьми версию игры до 1.17 (1.16.5 и старше) — она запустится на Java 8."
        )


def clear_cache() -> None:
    _cache.clear()


def candidate_paths() -> list[Path]:
    candidates: list[Path] = []
    for name in ("javaw.exe", "java.exe"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))

    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidates.append(Path(java_home) / "bin" / "javaw.exe")
        candidates.append(Path(java_home) / "bin" / "java.exe")

    managed = paths.java_root()
    if managed.is_dir():
        for executable in managed.glob("*/bin/javaw.exe"):
            candidates.append(executable)

    roots = [
        Path("C:/Program Files/Java"),
        Path("C:/Program Files (x86)/Java"),
        Path("C:/Program Files/Eclipse Adoptium"),
        Path("C:/Program Files/Microsoft"),
        Path("C:/Program Files/Amazon Corretto"),
        Path("C:/Program Files/Zulu"),
        Path("C:/Program Files/BellSoft"),
        Path(os.environ.get("LOCALAPPDATA", "") or "C:/Users/Public") / "Programs",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        for pattern in ("*/bin/javaw.exe", "**/bin/javaw.exe"):
            candidates.extend(root.glob(pattern))

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path).lower()
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        unique.append(path)
    return unique


def java_major(path: Path) -> int:
    try:
        result = subprocess.run(
            [str(path), "-version"],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
        text = (result.stderr or "") + (result.stdout or "")
        match = re.search(r'version "(\d+)(?:\.(\d+))?', text)
        if not match:
            return 0
        first = int(match.group(1))
        if first == 1:
            return int(match.group(2) or 0)
        return first
    except (OSError, subprocess.SubprocessError):
        return 0


def find_java(needed: int = JAVA_MIN, preferred: str = "") -> tuple[Path, int] | None:
    """Самая младшая Java, которой хватает версии. Предпочтение — путь из настроек."""
    needed = max(JAVA_MIN, int(needed or JAVA_MIN))
    key = (needed, preferred or "")
    if key in _cache:
        return _cache[key]

    candidates: list[Path] = []
    if preferred:
        path = Path(preferred)
        if path.is_file():
            candidates.append(path)
            for sibling in ("javaw.exe", "java.exe"):
                near = path.with_name(sibling)
                if near.is_file():
                    candidates.append(near)
    candidates.extend(candidate_paths())

    found: list[tuple[int, Path]] = []
    seen: set[str] = set()
    for path in candidates:
        marker = str(path).lower()
        if marker in seen:
            continue
        seen.add(marker)
        major = java_major(path)
        if major >= needed:
            found.append((major, path))

    result = None
    if found:
        found.sort(key=lambda item: (item[0], 0 if item[1].name.lower().startswith("javaw") else 1))
        major, path = found[0]
        result = (path, major)
    _cache[key] = result
    return result


def is_managed(path: Path) -> bool:
    try:
        return paths.java_root() in Path(path).resolve().parents
    except OSError:
        return False


def _download_url(needed: int) -> tuple[str, str, int]:
    data = net.fetch_json(ADOPTIUM.format(major=needed) + ADOPTIUM_QUERY)
    if not isinstance(data, list) or not data:
        raise net.DownloadError(f"Adoptium не предлагает JRE {needed}")
    package = ((data[0].get("binary") or {}).get("package") or {})
    link = package.get("link")
    checksum = package.get("checksum") or ""
    size = int(package.get("size") or 0)
    if not link:
        raise net.DownloadError("Adoptium вернул пустую ссылку")
    return link, checksum, size


def _extract(archive: Path, target: Path) -> Path | None:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".java-extract-", dir=target.parent) as folder:
        staging = Path(folder) / "runtime"
        staging.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            members = [info for info in bundle.infolist() if not info.is_dir()]
            if not members:
                return None
            roots = {info.filename.split("/")[0] for info in members}
            root = roots.pop() if len(roots) == 1 else ""
            safe_root = safepath.safe_relative(root) if root else None
            prefix = (
                root + "/"
                if safe_root is not None
                and len(safe_root.parts) == 1
                and safe_root.parts[0] == root
                else ""
            )
            report = safepath.extract_zip(
                bundle,
                (info.filename for info in members),
                staging,
                strip=(prefix,) if prefix else (),
                max_files=50_000,
                max_bytes=4 * 1024 ** 3,
            )
            if report["skipped"]:
                log.warning("пропущено небезопасных файлов в архиве Java: %s", report["skipped"])
                raise safepath.PathError("В архиве Java обнаружены небезопасные пути")
            if not report["written"]:
                return None

        backup = Path(folder) / "previous"
        had_target = target.exists()
        if had_target:
            os.replace(target, backup)
        try:
            os.replace(staging, target)
        except OSError:
            if had_target and backup.exists():
                os.replace(backup, target)
            raise
    return target


def provision(
    needed: int,
    log=None,
    cancel: threading.Event | None = None,
    on_progress=None,
) -> tuple[Path, int]:
    """Скачивает официальную JRE нужной версии с Adoptium и возвращает javaw.exe."""
    log = log or (lambda message: None)
    needed = max(JAVA_MIN, int(needed))
    check_java_supported(needed)
    root = paths.java_root()
    root.mkdir(parents=True, exist_ok=True)

    link, checksum, size = _download_url(needed)
    archive = root / f"jre-{needed}.zip"
    log(f"Скачиваю Java {needed} с Adoptium ({net_size(size)})…")
    net.download(link, archive, None, size or None, on_bytes=on_progress, cancel=cancel)

    if checksum:
        actual = net.sha256_file(archive)
        if actual.lower() != checksum.lower():
            archive.unlink(missing_ok=True)
            raise net.ChecksumError(f"Контрольная сумма JRE {needed} не совпала")

    target = root / str(needed)
    log(f"Распаковываю Java {needed}…")
    extracted = _extract(archive, target)
    archive.unlink(missing_ok=True)
    if extracted is None:
        raise net.DownloadError(f"Архив Java {needed} пуст")

    javaw = extracted / "bin" / "javaw.exe"
    if not javaw.is_file():
        found = list(extracted.glob("*/bin/javaw.exe"))
        if not found:
            raise net.DownloadError(f"В архиве Java {needed} нет javaw.exe")
        javaw = found[0]
    major = java_major(javaw) or needed
    _cache.clear()
    return javaw, major


def ensure_java(
    needed: int,
    preferred: str = "",
    log=None,
    cancel: threading.Event | None = None,
    on_progress=None,
) -> tuple[Path, int]:
    """Находит подходящую Java, а если её нет — ставит официальную JRE рядом с лаунчером."""
    found = find_java(needed, preferred)
    if found:
        return found
    if cancel is not None and cancel.is_set():
        raise net.DownloadCancelled()
    check_java_supported(needed)
    return provision(needed, log=log, cancel=cancel, on_progress=on_progress)


def net_size(size: int) -> str:
    if not size:
        return "неизвестный размер"
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}".replace(".", ",")
        value /= 1024
    return f"{value:.1f} ГБ"
