"""Разбор последнего краш-лога Minecraft.

Ищем папку crash-reports, берём самый свежий .txt и вытаскиваем описание,
цепочку Caused by и подозреваемые моды. Ничего не меняем, только читаем.
"""

from __future__ import annotations

from pathlib import Path

MAX_CAUSED = 3
MAX_MODS = 8


def reports_dir(data_dir) -> Path:
    return Path(data_dir) / "crash-reports"


def latest(data_dir) -> Path | None:
    folder = reports_dir(data_dir)
    if not folder.is_dir():
        return None
    files = [path for path in folder.glob("*.txt") if path.is_file()]
    if not files:
        return None

    def modified(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    return max(files, key=modified)


def analyze(text: str) -> dict:
    summary = ""
    caused: list[str] = []
    mods: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        low = line.lower()
        if not summary and low.startswith("description:"):
            summary = line.split(":", 1)[1].strip()
        elif low.startswith("caused by:"):
            value = line.split(":", 1)[1].strip()
            if value and value not in caused:
                caused.append(value)
        elif low.startswith("suspected mods:"):
            value = line.split(":", 1)[1].strip()
            for part in value.split(","):
                name = part.strip()
                if name and name not in mods:
                    mods.append(name)
    if not summary:
        summary = next((line.strip() for line in str(text or "").splitlines()
                        if line.strip()), "")
    return {
        "summary": summary[:200],
        "caused": caused[:MAX_CAUSED],
        "mods": mods[:MAX_MODS],
    }


def scan(data_dir) -> dict | None:
    path = latest(data_dir)
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    result = analyze(text)
    result["name"] = path.name
    result["path"] = str(path)
    return result
