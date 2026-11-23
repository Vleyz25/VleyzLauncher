"""Разбор последнего краш-лога Minecraft.

Ищем папку crash-reports, берём самый свежий .txt и вытаскиваем описание,
цепочку Caused by, подозреваемые моды и разбор причины: что именно сломалось
и что с этим делать. Ничего не меняем, только читаем.
"""

from __future__ import annotations

import re
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


def _first_match(text: str, pattern: str) -> str:
    found = re.search(pattern, text, re.IGNORECASE)
    return found.group(1).strip() if found else ""


def diagnose(text: str) -> dict:
    """Возвращает причину и совет по свежему крашу.

    Разбираем типовые причины: несовместимый мод, мало памяти, чужая Java,
    нехватка зависимостей, дубли модов, конфликт миксинов.
    """
    body = str(text or "")

    mod = _first_match(body, r"Failed to create model for ([A-Za-z0-9_.\-]+):")
    if mod:
        part = _first_match(body, r"Can't find part ([A-Za-z0-9_.\-]+)")
        detail = f" (модель ищет часть «{part}»)" if part else ""
        return {
            "cause": f"мод {mod} несовместим с этой версией игры{detail}",
            "mod": mod,
            "advice": f"Отключи мод {mod} или поставь его сборку под эту версию Minecraft — "
                      "игра падает до меню, сам лаунчер тут ни при чём.",
        }

    if re.search(r"java\.lang\.OutOfMemoryError", body):
        need = _first_match(body, r"Failed to allocate (\d+) ?bytes")
        extra = f" (не удалось выделить {need} байт)" if need else ""
        return {
            "cause": "игре не хватило памяти" + extra,
            "mod": "",
            "advice": "Увеличь память в настройках лаунчера (например, 4096 МБ) и закрой лишние "
                      "программы. Если модов много — оставь 4096–6144 МБ.",
        }

    if re.search(r"UnsupportedClassVersionError|class file major version", body):
        version = _first_match(body, r"major version (\d+)")
        return {
            "cause": "мод собран под другую версию Java" + (f" (major {version})" if version else ""),
            "mod": "",
            "advice": "Проверь версию игры и загрузчик: сборка мода должна совпадать с версией "
                      "Minecraft и Java из списка версий.",
        }

    if re.search(r"Missing or unsupported mandatory dependencies", body):
        missing = re.findall(r"Mod ID:\s*'?([A-Za-z0-9_.\-]+)'?", body)
        names = ", ".join(dict.fromkeys(missing))[:120] if missing else ""
        return {
            "cause": "не хватает модов-зависимостей" + (f": {names}" if names else ""),
            "mod": missing[0] if missing else "",
            "advice": "Установи недостающие моды (их список есть в отчёте) или отключи мод, "
                      "которому они нужны.",
        }

    if re.search(r"duplicate mods|Found duplicate", body, re.IGNORECASE):
        return {
            "cause": "в папке модов лежат два одинаковых мода",
            "mod": "",
            "advice": "Открой «Моды» в лаунчере: проверка здоровья покажет дубли — оставь одну копию.",
        }

    if re.search(r"Mixin apply failed|mixin\.injection|MixinTransformerError", body, re.IGNORECASE):
        mod = _first_match(body, r"from mod ([A-Za-z0-9_.\-]+)")
        return {
            "cause": "мод конфликтует с игрой или другим модом (ошибка миксина)",
            "mod": mod,
            "advice": (f"Отключи мод {mod} и запусти игру." if mod
                       else "Отключай моды по одному, чтобы найти конфликтующий."),
        }

    if re.search(r"NoClassDefFoundError|NoSuchMethodError|NoSuchFieldError", body):
        missing = _first_match(body, r"(?:NoClassDefFoundError|NoSuchMethodError|NoSuchFieldError): ([^\s]+)")
        return {
            "cause": "мод обращается к тому, чего нет в этой версии игры" + (f" ({missing})" if missing else ""),
            "mod": "",
            "advice": "Проверь, что все моды одной версии игры и загрузчика; лишние отключи.",
        }

    return {"cause": "", "mod": "", "advice": ""}


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
    diagnosis = diagnose(text)
    if diagnosis["mod"] and diagnosis["mod"] not in mods:
        mods.insert(0, diagnosis["mod"])
    return {
        "summary": summary[:200],
        "caused": caused[:MAX_CAUSED],
        "mods": mods[:MAX_MODS],
        "cause": diagnosis["cause"],
        "advice": diagnosis["advice"],
        "mod": diagnosis["mod"],
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
