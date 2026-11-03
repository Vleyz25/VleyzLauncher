"""Проверка, что версия приложения совпадает во всех местах сборки."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> int:
    print(f"ОШИБКА: {message}")
    return 1


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from vleyzlauncher import APP_VERSION

    print(f"версия в коде: {APP_VERSION}")

    for name in ("version_info.txt", "version_info_setup.txt"):
        path = ROOT / "tools" / name
        text = path.read_text(encoding="utf-8")
        found = {tuple(int(part) for part in match.split(",")) for match in
                 re.findall(r"(?:filevers|prodvers)=\(([^)]+)\)", text)}
        expected = tuple(int(part) for part in re.findall(r"\d+", APP_VERSION))
        expected += (0,) * (4 - len(expected))
        if found != {expected}:
            return fail(f"{name}: {sorted(found)} != {expected}")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    first = re.search(r"^## \[([^\]]+)\]", changelog, re.MULTILINE)
    if not first:
        return fail("в CHANGELOG.md нет заголовка версии")
    if first.group(1) != APP_VERSION:
        return fail(f"CHANGELOG.md: {first.group(1)} != {APP_VERSION}")

    print("все версии совпадают")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
