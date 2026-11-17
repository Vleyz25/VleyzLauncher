"""Установка модпака из каталога Modrinth: файл → новая сборка.

Скачиваем выбранную версию .mrpack во временную папку, разбираем её
штатным установщиком модпаков и кладём в отдельный инстанс, чтобы не
смешать с уже установленными сборками.

Важно: сам пак — это только моды и конфиги. Версию игры и загрузчик
ставим отдельно, иначе сборка лежит в ``instances``, но её не видно в
«Библиотеке»: там список строится по папке ``versions``.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from . import installer as installer_core
from . import instances, loaders, modpacks, net, safepath


class PackInstallError(RuntimeError):
    pass


def unique_instance(data_dir, game: str, loader: str, slug: str = "") -> str:
    """Свободный id сборки: game-loader, при занятости — с суффиксом."""
    existing = {str(row["id"]) for row in loaders.installed_versions(Path(data_dir))}
    base = f"{game}-{loader}" if game and loader else "pack"
    if base not in existing:
        return base
    suffix = safepath.sanitize_name((slug or "pack").replace(" ", "-"), "pack")[:24] or "pack"
    candidate = f"{base}-{suffix}"
    number = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}-{number}"
        number += 1
    return candidate


def _merge_legacy(source: Path, target: Path, log) -> None:
    """Переносит файлы старой папки сборки в новую, не затирая существующие."""
    if not source.is_dir() or source.resolve() == target.resolve():
        return
    moved = 0
    for item in source.rglob("*"):
        if not item.is_file():
            continue
        destination = target / item.relative_to(source)
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(item), str(destination))
            moved += 1
        except OSError:
            continue
    if moved:
        log(f"Перенёс {moved} файлов из старой папки сборки {source.name}")


def install(version: dict, data_dir, log=None, cancel=None,
            instance_id: str | None = None) -> dict:
    url = str(version.get("url") or "")
    if not url:
        raise PackInstallError("у выбранной версии нет файла для скачивания")
    log_fn = log or (lambda message: None)
    data_dir = Path(data_dir)
    with tempfile.TemporaryDirectory(prefix="vleyz-pack-") as folder:
        name = safepath.sanitize_name(str(version.get("filename") or "pack.mrpack"),
                                      "pack.mrpack")
        archive = Path(folder) / name
        net.download(url, archive, version.get("sha1"),
                     int(version.get("size") or 0) or None,
                     cancel=cancel, sha512=version.get("sha512") or None)
        plan = modpacks.inspect(archive)
        game = str(plan.get("game_version") or "")
        loader = str(plan.get("loader") or "")
        target = str(instance_id or "")

        if not target and game and loader in installer_core.loaders.LOADER_BY_ID:
            log_fn(f"Ставлю Minecraft {game} · {loader} для этой сборки")
            runner = installer_core.Installer(data_dir, installer_core.Progress(),
                                              log=log_fn, cancel=cancel)
            installed = runner.install(game, loader)
            target = str(installed.get("version_id") or "")

        if not target:
            target = unique_instance(data_dir, game, loader,
                                     str(version.get("name") or ""))

        legacy = instances.path(data_dir, f"{game}-{loader}") if game and loader else None
        result = modpacks.install(data_dir, target, archive, log_fn=log, cancel=cancel)
        if legacy is not None:
            _merge_legacy(legacy, instances.path(data_dir, target), log_fn)

    result["instance"] = target
    result["pack_name"] = str(plan.get("name") or "")
    result["game_version"] = game
    result["loader"] = loader
    return result
