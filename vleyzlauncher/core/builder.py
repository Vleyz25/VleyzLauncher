"""Генератор сборок: запрос словами → план модов под версию и загрузчик.

Правила работают всегда и без сети, кроме самого поиска в каталоге. Если
у пользователя настроен GigaChat, его ответ лишь уточняет намерение
(категории и число модов), а конкретные проекты всё равно берутся из
Modrinth — модель не придумывает id.
"""

from __future__ import annotations

import json
import re
import time
import zipfile
from pathlib import Path

from . import catalog, gigachat, modrinth

SUPPORTED_LOADERS = ("fabric", "forge", "neoforge")
DEFAULT_LIMIT = 15
MAX_LIMIT = 40
MIN_LIMIT = 5
PER_CATEGORY = 6

KEYWORDS: dict[str, tuple[str, ...]] = {
    "optimization": ("оптимиз", "fps", "фпс", "лаг", "провис", "слаб", "ноут",
                     "производ", "быстр", "тормоз", "тянет"),
    "technology": ("техно", "индустр", "механиз", "автоматиз", "энерг", "реактор",
                   "завод", "фабрик", "генератор"),
    "magic": ("маги", "волшеб", "заклин", "чары", "колдун", "некромант", "друид",
              "ритуал"),
    "adventure": ("приключ", "квест", "данж", "подземел", "исслед", "поход",
                  "сокровищ", "руин", "средневек", "рыцар", "замок", "фэнтези",
                  "скайрим"),
    "decoration": ("красив", "декор", "мебел", "строит", "интерьер", "уют",
                   "украш", "дом"),
    "worldgen": ("биом", "генерац", "ландшафт", "мир", "пещер", "измерен", "космос",
                 "планет", "луна", "марс"),
    "storage": ("хранен", "сундук", "склад", "сортиров", "ящик", "контейнер",
                "инвентар"),
    "food": ("еда", "еду", "кулинар", "кухн", "рецепт", "сыт", "ферм", "готовк"),
    "utility": ("утилит", "удобств", "qol", "минимап", "карта", "интерфейс"),
    "mobs": ("моб", "животн", "питомц", "дракон", "зомби", "босс"),
    "equipment": ("оружи", "меч", "лук", "броня", "доспех", "снаряжен", "бой",
                  "сражен", "pvp", "пвп"),
    "transportation": ("транспорт", "поезд", "самолет", "машин", "лодк", "автомобил",
                       "вертолет", "кораб"),
    "game-mechanics": ("механик", "физик", "гравитац"),
    "minigame": ("мини-игр", "мини игр", "минигейм"),
    "social": ("друз", "кооп", "мультиплеер"),
    "economy": ("эконом", "магазин", "торговл", "деньг", "валют", "аукцион"),
    "management": ("управлен", "поселен", "город", "деревн", "колони"),
}

PRESETS: tuple[dict, ...] = (
    {"id": "optimization", "title": "Оптимизация", "limit": 14,
     "categories": ("optimization", "utility")},
    {"id": "pvp", "title": "PvP", "limit": 14,
     "categories": ("optimization", "equipment", "utility")},
    {"id": "tech", "title": "Техно", "limit": 18,
     "categories": ("technology", "storage", "utility")},
    {"id": "magic", "title": "Магия", "limit": 16,
     "categories": ("magic", "adventure", "decoration")},
    {"id": "adventure", "title": "Приключения", "limit": 18,
     "categories": ("adventure", "worldgen", "mobs")},
    {"id": "cozy", "title": "Уют и декор", "limit": 16,
     "categories": ("decoration", "food", "utility")},
    {"id": "vanilla", "title": "Vanilla+", "limit": 15,
     "categories": ("utility", "optimization", "decoration")},
)

PRESET_BY_ID = {preset["id"]: preset for preset in PRESETS}


class BuilderError(RuntimeError):
    pass


def clamp_limit(value: object, default: int = DEFAULT_LIMIT) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(MIN_LIMIT, min(MAX_LIMIT, number))


def parse_intent(query: str) -> dict:
    """Разбирает текст запроса без сети: категории, лимит, шейдеры.

    Категории сортируются по числу совпавших слов, при равенстве — по тому,
    что упомянуто раньше. Поэтому «магия для слабого ноутбука» — это магия,
    а оптимизация только вторая, а не наоборот.
    """
    text = str(query or "").lower().replace("ё", "е")
    found: dict[str, tuple[int, int]] = {}
    for category, words in KEYWORDS.items():
        hits = [text.find(word) for word in words if word in text]
        if hits:
            found[category] = (len(hits), min(hits))
    pvp = any(word in text for word in ("pvp", "пвп"))
    categories = [
        category for category, _ in sorted(
            found.items(), key=lambda item: (item[1][1], -item[1][0])
        )
    ]
    if pvp:
        categories = ["equipment", "optimization", "utility"] + [
            item for item in categories
            if item not in ("equipment", "optimization", "utility")
        ]
    matched = bool(categories)
    if not matched:
        categories = ["utility", "optimization", "decoration"]

    limit = DEFAULT_LIMIT
    match = re.search(r"(\d{1,2})\s*(?:мод|mod)", text)
    if match:
        limit = clamp_limit(match.group(1))
    elif "много" in text:
        limit = 30
    elif any(word in text for word in ("мало", "легк", "минимал")):
        limit = 10

    shaders = any(word in text for word in ("шейдер", "shader", "график"))
    if pvp:
        title = "PvP"
    elif matched:
        title = f"Сборка · {catalog.category_label(categories[0])}"
    else:
        title = "Vanilla+"
    return {
        "categories": categories,
        "limit": limit,
        "shaders": shaders,
        "title": title,
        "source": "rules",
        "matched": matched,
    }


def intent_from_preset(preset: dict) -> dict:
    """Намерение из встроенного или плагинного пресета."""
    categories = [
        str(item) for item in (preset.get("categories") or [])
        if str(item) in ALLOWED_CATEGORIES
    ]
    return {
        "categories": categories[:4] or ["optimization"],
        "limit": clamp_limit(preset.get("limit")),
        "shaders": bool(preset.get("shaders")),
        "title": " ".join(str(preset.get("title") or "Сборка").split())[:48],
        "source": "preset",
    }


def preset_intent(preset_id: str) -> dict:
    preset = PRESET_BY_ID.get(str(preset_id or ""))
    if preset is None:
        raise BuilderError(f"неизвестный пресет: {preset_id}")
    return intent_from_preset(preset)


def select(intent: dict, game_version: str, loader: str, skip: int = 0) -> list[dict]:
    """Ищет моды по категориям намерения. Дубли и лишнее отбрасываются.

    skip сдвигает окно выдачи — так кнопка «Ещё вариант» даёт другие моды
    по тому же запросу.
    """
    loader = str(loader or "").lower()
    if loader not in SUPPORTED_LOADERS:
        raise BuilderError("Сборка возможна только с Fabric, Forge или NeoForge")
    limit = clamp_limit(intent.get("limit"))
    skip = max(0, int(skip or 0))
    seen: set[str] = set()
    items: list[dict] = []
    for category in intent.get("categories") or []:
        page = catalog.search("", game_version, loader, category, "", "downloads", 0, 20)
        hits = list(page.get("items") or [])
        if hits and skip:
            # Кольцо: если сдвиг вышел за страницу, продолжаем с начала.
            shift = skip % len(hits)
            hits = hits[shift:] + hits[:shift]
        for hit in hits[:PER_CATEGORY]:
            if len(items) >= limit:
                break
            project_id = str(hit.get("id") or "")
            if not project_id or project_id in seen:
                continue
            seen.add(project_id)
            row = dict(hit)
            row["reason"] = catalog.category_label(category)
            items.append(row)
        if len(items) >= limit:
            break
    return items


def build(query: str, game_version: str, loader: str, limit: int | None = None) -> dict:
    intent = parse_intent(query)
    if limit:
        intent["limit"] = clamp_limit(limit)
    items = select(intent, game_version, loader)
    notes: list[str] = []
    if not items:
        notes.append("Под эти условия моды не нашлись — попробуйте другую версию игры.")
    if intent.get("shaders"):
        notes.append("Шейдеры ставятся отдельно: вкладка «Моды» → «Паки».")
    return {"title": intent["title"], "intent": intent, "items": items, "notes": notes}


ALLOWED_CATEGORIES = tuple(slug for slug, _label in catalog.CATEGORIES if slug)

SYSTEM_PROMPT = (
    "Ты собираешь сборку модов Minecraft. Ответь строго одним JSON-объектом "
    'вида {"categories": ["optimization"], "limit": 15, "shaders": false, '
    '"title": "Короткое название"}. '
    "categories — только из списка: " + ", ".join(ALLOWED_CATEGORIES) + ". "
    "limit — целое от 5 до 40. Без пояснений и markdown."
)


def intent_from_json(answer: str) -> dict:
    """Достаёт JSON из ответа модели и приводит его к нашему намерению."""
    text = str(answer or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise BuilderError("GigaChat вернул не JSON")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise BuilderError("GigaChat вернул битый JSON") from exc
    if not isinstance(data, dict):
        raise BuilderError("GigaChat вернул не объект")
    categories = [
        str(item) for item in (data.get("categories") or [])
        if str(item) in ALLOWED_CATEGORIES
    ]
    if not categories:
        raise BuilderError("GigaChat не назвал ни одной известной категории")
    title = " ".join(str(data.get("title") or "").split())[:48] or "Сборка по запросу"
    return {
        "categories": categories[:4],
        "limit": clamp_limit(data.get("limit")),
        "shaders": bool(data.get("shaders")),
        "title": title,
        "source": "gigachat",
    }


def parse_with_gigachat(client: gigachat.GigaChat, query: str,
                        game_version: str, loader: str) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Запрос: {query}\nВерсия игры: {game_version}\n"
                                    f"Загрузчик: {loader}"},
    ]
    return intent_from_json(client.ask(messages, temperature=0.1, max_tokens=300))


def backup_mods(destination, backups_dir) -> Path | None:
    """Архив текущих .jar перед массовой установкой. None — архивировать нечего."""
    destination = Path(destination)
    if not destination.is_dir():
        return None
    files = [file for file in destination.rglob("*.jar") if file.is_file()]
    if not files:
        return None
    backups_dir = Path(backups_dir)
    backups_dir.mkdir(parents=True, exist_ok=True)
    target = backups_dir / f"mods-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            try:
                archive.write(file, file.relative_to(destination))
            except OSError:
                continue
    return target


def install(items: list[dict], destination, game_version: str, loader: str,
            log=None, cancel=None) -> dict:
    """Скачивает моды и обязательные зависимости, без повторов файлов."""
    plan: list[dict] = []
    seen: set[str] = set()
    for item in items:
        project_id = str(item.get("id") or "")
        if not project_id:
            continue
        for entry in modrinth.resolve(project_id, game_version, loader):
            key = str(entry.get("sha1") or entry.get("filename") or "")
            if key in seen:
                continue
            seen.add(key)
            plan.append(entry)
    if not plan:
        raise BuilderError("Не удалось собрать список файлов")
    return modrinth.install(plan, destination, log=log, cancel=cancel)
