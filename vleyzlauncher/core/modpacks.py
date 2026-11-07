"""Импорт готовых сборок: формат Modrinth (``.mrpack``) и обычные zip.

Сборка ставится в выбранный инстанс: моды уходят в его ``mods``, а файлы из
``overrides``/``client-overrides`` раскладываются по папкам сборки. Формат
CurseForge читается, но без ключа API файлы оттуда не скачать — такие сборки
разбираются только на overrides, о чём честно сообщается в предупреждениях.
"""

from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path, PurePosixPath

from . import instances, net, safepath

log = logging.getLogger(__name__)

INDEX_NAME = "modrinth.index.json"
META_NAME = ".vleyz-modpack.json"
CF_MANIFEST = "manifest.json"
MAX_FILES = 4000


class ModpackError(Exception):
    pass


def _safe_member(name: str) -> str | None:
    """Путь внутри сборки без выхода из папки инстанса."""
    safe = safepath.safe_relative(name)
    return str(safe) if safe is not None else None


def _loader_from_deps(deps: dict) -> tuple[str, str]:
    """Определяет загрузчик и его версию по dependencies из индекса."""
    for key in ("fabric-loader", "quilt-loader"):
        if deps.get(key):
            return ("fabric" if key.startswith("fabric") else "quilt"), str(deps[key])
    if deps.get("neoforge"):
        return "neoforge", str(deps["neoforge"])
    if deps.get("forge"):
        return "forge", str(deps["forge"])
    return "vanilla", ""


def _read_json(archive: zipfile.ZipFile, name: str) -> dict | None:
    try:
        with archive.open(name) as handle:
            data = json.loads(handle.read().decode("utf-8"))
    except (KeyError, ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def inspect(path: Path) -> dict:
    """Разбирает архив и возвращает описание сборки без установки."""
    path = Path(path)
    if not path.is_file():
        raise ModpackError("Файл сборки не найден")
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ModpackError("Это не zip-архив сборки") from exc

    with archive:
        names = archive.namelist()
        index = _read_json(archive, INDEX_NAME) if INDEX_NAME in names else None
        if index is not None:
            deps = index.get("dependencies") or {}
            loader, loader_version = _loader_from_deps(deps)
            files = []
            for item in index.get("files") or []:
                if not isinstance(item, dict):
                    continue
                target = _safe_member(item.get("path") or "")
                if not target:
                    continue
                env = item.get("env") or {}
                if str(env.get("client") or "").lower() == "unsupported":
                    continue
                downloads = [str(url) for url in (item.get("downloads") or []) if url]
                if not downloads:
                    continue
                hashes = item.get("hashes") or {}
                files.append(
                    {
                        "path": target,
                        "url": downloads[0],
                        "sha1": str(hashes.get("sha1") or "") or None,
                        "size": int(item.get("fileSize") or 0) or None,
                    }
                )
            return {
                "kind": "modrinth",
                "name": str(index.get("name") or path.stem),
                "version": str(index.get("versionId") or ""),
                "game_version": str(deps.get("minecraft") or ""),
                "loader": loader,
                "loader_version": loader_version,
                "summary": str(index.get("summary") or ""),
                "files": files[:MAX_FILES],
                "overrides": [n for n in names if n.startswith(("overrides/", "client-overrides/"))],
                "warnings": [],
            }

        if CF_MANIFEST in names:
            manifest = _read_json(archive, CF_MANIFEST) or {}
            deps = {}
            for item in manifest.get("minecraft") or []:
                pass
            minecraft = manifest.get("minecraft") or {}
            game = ""
            if isinstance(minecraft, dict):
                versions = minecraft.get("version") or []
                if versions:
                    game = str(versions[0])
            mod_loader = minecraft.get("modLoaders") or [] if isinstance(minecraft, dict) else []
            loader = "vanilla"
            loader_version = ""
            for entry in mod_loader:
                if not isinstance(entry, dict):
                    continue
                ident = str(entry.get("id") or "")
                if ident.startswith("forge-"):
                    loader, loader_version = "forge", ident.split("-", 1)[1]
                elif ident.startswith("neoforge-"):
                    loader, loader_version = "neoforge", ident.split("-", 1)[1]
                elif ident.startswith("fabric-"):
                    loader, loader_version = "fabric", ident.split("-", 1)[1]
                if loader != "vanilla":
                    break
            return {
                "kind": "curseforge",
                "name": str(manifest.get("name") or path.stem),
                "version": str(manifest.get("version") or ""),
                "game_version": game,
                "loader": loader,
                "loader_version": loader_version,
                "summary": str(manifest.get("author") or ""),
                "files": [],
                "overrides": [n for n in names if n.startswith("overrides/")],
                "warnings": [
                    "Сборка CurseForge: файлы модов не скачать без ключа API, "
                    "перенесены только overrides"
                ],
            }

        mods = [n for n in names if n.lower().startswith("mods/") and n.lower().endswith(".jar")]
        if not mods:
            raise ModpackError("В архиве нет ни modrinth.index.json, ни папки mods")
        return {
            "kind": "zip",
            "name": path.stem,
            "version": "",
            "game_version": "",
            "loader": "",
            "loader_version": "",
            "summary": "",
            "files": [],
            "overrides": names,
            "warnings": [],
        }


def _extract(archive: zipfile.ZipFile, members: list[str], target: Path, strip: tuple[str, ...]) -> int:
    """Распаковка через safepath: без выходов из папки и с лимитами."""
    report = safepath.extract_zip(archive, members, target, strip=strip)
    if report["skipped"]:
        log.info("пропущено опасных файлов в архиве: %s", report["skipped"])
    return report["written"]


def install(
    data_dir: Path,
    instance_id: str,
    archive_path: Path,
    log_fn=None,
    cancel=None,
    on_progress=None,
) -> dict:
    """Ставит сборку в инстанс. Возвращает отчёт об установке."""
    log_fn = log_fn or (lambda message: None)
    plan = inspect(archive_path)
    target = instances.ensure(data_dir, instance_id)
    safepath.require_space(target, sum(int(item.get("size") or 0) for item in plan["files"]))

    installed = 0
    skipped = 0
    files = plan["files"]
    total = len(files)
    for index, item in enumerate(files, start=1):
        if cancel is not None and cancel.is_set():
            raise net.DownloadCancelled()
        destination = target / item["path"]
        if net.file_is_valid(destination, item.get("sha1"), item.get("size")):
            skipped += 1
            log_fn(f"[{index}/{total}] уже на месте: {PurePosixPath(item['path']).name}")
        else:
            log_fn(f"[{index}/{total}] скачиваю {PurePosixPath(item['path']).name}")
            net.download(
                str(item["url"]),
                destination,
                item.get("sha1"),
                item.get("size"),
                cancel=cancel,
            )
            installed += 1
        if on_progress is not None:
            on_progress(index, total, item)

    extracted = 0
    if plan["overrides"]:
        try:
            with zipfile.ZipFile(Path(archive_path)) as archive:
                strip = ("client-overrides/", "overrides/") if plan["kind"] != "zip" else ("",)
                extracted = _extract(archive, plan["overrides"], target, strip)
        except (zipfile.BadZipFile, OSError) as exc:
            raise ModpackError(f"Не удалось распаковать сборку: {exc}") from exc
        log_fn(f"распаковано файлов: {extracted}")

    meta = {
        "name": plan["name"],
        "version": plan["version"],
        "game_version": plan["game_version"],
        "loader": plan["loader"],
        "loader_version": plan["loader_version"],
        "kind": plan["kind"],
        "installed": installed,
        "skipped": skipped,
        "extracted": extracted,
    }
    try:
        (target / META_NAME).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass

    return {
        "instance": instance_id,
        "name": plan["name"],
        "installed": installed,
        "skipped": skipped,
        "extracted": extracted,
        "warnings": list(plan["warnings"]),
    }


def installed_packs(data_dir: Path, instance_id: str) -> list[dict]:
    """Сборки, которые уже стоят в инстансе (по мета-файлу)."""
    target = instances.path(data_dir, instance_id) / META_NAME
    if not target.is_file():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    data["instance"] = instance_id
    return [data]
