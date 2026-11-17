"""Вытаскивает раздел CHANGELOG для текущей версии в файл для GitHub Release.

Если раздела нет — оставляет пустой файл, чтобы релиз опубликовался с
автоматическими заметками.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vleyzlauncher import APP_VERSION  # noqa: E402


def section(text: str, version: str) -> str:
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def main() -> None:
    changelog = ROOT / "CHANGELOG.md"
    tried = ""
    if changelog.is_file():
        tried = section(changelog.read_text(encoding="utf-8"), APP_VERSION)
    body = tried or (
        f"## Vleyz Launcher {APP_VERSION}\n\n"
        "Раздел в CHANGELOG.md не найден — подробности в истории изменений."
    )
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "release-notes.md"
    body += "\n"
    target.write_text(body, encoding="utf-8")
    print(f"{target.name}: {'раздел CHANGELOG' if tried else 'заглушка'}")


if __name__ == "__main__":
    main()
