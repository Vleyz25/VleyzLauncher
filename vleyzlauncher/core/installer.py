from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import zipfile
from collections import deque
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

from . import java as java_core
from . import loaders, net, versions
from .paths import human_size, human_speed

WORKERS = 16


class InstallError(Exception):
    pass


@dataclass
class Task:
    url: str
    dest: Path
    sha1: str | None = None
    size: int | None = None
    extract: bool = False
    exclude: tuple[str, ...] = field(default_factory=tuple)


class Progress:
    """Потокобезопасный счётчик прогресса, который читает UI."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.stage = "Ожидание"
        self.detail = ""
        self.total_bytes = 0
        self.done_bytes = 0
        self.total_files = 0
        self.done_files = 0
        self.indeterminate = False
        self._history: deque[tuple[float, int]] = deque(maxlen=24)
        self.started_at = 0.0
        self.finished_at = 0.0

    def reset(self) -> None:
        with self._lock:
            self.stage = "Подготовка"
            self.detail = ""
            self.total_bytes = 0
            self.done_bytes = 0
            self.total_files = 0
            self.done_files = 0
            self.indeterminate = False
            self._history.clear()
            self.started_at = time.time()
            self.finished_at = 0.0

    def add(self, count: int) -> None:
        with self._lock:
            self.done_bytes = max(0, self.done_bytes + count)
            self._history.append((time.time(), self.done_bytes))

    def set_total(self, total_bytes: int, total_files: int, done_bytes: int, done_files: int) -> None:
        with self._lock:
            self.total_bytes = max(0, total_bytes)
            self.total_files = max(0, total_files)
            self.done_bytes = done_bytes
            self.done_files = done_files

    def complete_file(self) -> None:
        with self._lock:
            self.done_files += 1

    def set_stage(self, stage: str, detail: str = "") -> None:
        with self._lock:
            self.stage = stage
            self.detail = detail

    def set_indeterminate(self, value: bool, detail: str = "") -> None:
        with self._lock:
            self.indeterminate = value
            if detail:
                self.detail = detail

    def finish(self) -> None:
        with self._lock:
            self.indeterminate = False
            self.done_bytes = self.total_bytes
            self.done_files = self.total_files
            self.finished_at = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            percent = 0.0
            if self.total_bytes > 0:
                percent = min(100.0, self.done_bytes * 100.0 / self.total_bytes)
            elif self.total_bytes == 0 and self.done_files > 0 and not self.indeterminate:
                percent = 100.0
            speed = 0.0
            if len(self._history) >= 2:
                t0, b0 = self._history[0]
                t1, b1 = self._history[-1]
                if t1 - t0 > 0.25:
                    speed = (b1 - b0) / (t1 - t0)
            elapsed = (self.finished_at or time.time()) - self.started_at if self.started_at else 0.0
            return {
                "stage": self.stage,
                "detail": self.detail,
                "percent": percent,
                "indeterminate": self.indeterminate,
                "total_bytes": self.total_bytes,
                "done_bytes": self.done_bytes,
                "total_files": self.total_files,
                "done_files": self.done_files,
                "speed": speed,
                "speed_text": human_speed(speed),
                "elapsed": elapsed,
                "downloaded_text": f"{human_size(self.done_bytes)} из {human_size(self.total_bytes)}",
            }


class Installer:
    def __init__(self, data_dir: Path, progress: Progress, log=None, cancel: threading.Event | None = None):
        self.data_dir = Path(data_dir)
        self.progress = progress
        self.log = log or (lambda message: None)
        self.cancel = cancel or threading.Event()

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise net.DownloadCancelled()

    # ------------------------------------------------------- сбор задач

    def _collect(self, version_id: str) -> tuple[dict, list[Task], Path]:
        """Ванильные файлы: клиент, библиотеки, нативы, лог-конфиг, ассеты."""
        data_dir = self.data_dir
        version_dir = data_dir / "versions" / version_id
        self.progress.set_stage("Запрашиваю официальный манифест Mojang", "launchermeta.mojang.com")
        self._check_cancel()
        vjson = versions.ensure_version_json(data_dir, version_id, cancel=self.cancel)

        tasks: list[Task] = []
        client = (vjson.get("downloads") or {})["client"]
        jar_path = version_dir / f"{version_id}.jar"
        tasks.append(Task(client["url"], jar_path, client.get("sha1"), client.get("size")))

        logging_cfg = ((vjson.get("logging") or {}).get("client") or {}).get("file")
        if logging_cfg and logging_cfg.get("url"):
            name = logging_cfg.get("id") or "client-logging"
            tasks.append(
                Task(
                    logging_cfg["url"],
                    data_dir / "assets" / "log_configs" / f"{name}",
                    logging_cfg.get("sha1"),
                    logging_cfg.get("size"),
                )
            )

        natives_dir = version_dir / "natives"
        for lib in vjson.get("libraries", []):
            if not versions.rules_allow(lib.get("rules")):
                continue
            tasks.extend(self._library_tasks(lib))

        tasks.extend(self._asset_tasks(vjson))
        unique = self._dedupe(tasks)
        return vjson, unique, natives_dir

    def _library_tasks(self, lib: dict) -> list[Task]:
        data_dir = self.data_dir
        result: list[Task] = []
        downloads = lib.get("downloads") or {}
        natives_key = (lib.get("natives") or {}).get(versions.OS_NAME)
        if natives_key:
            natives_key = natives_key.replace("${arch}", "64")
            info = (downloads.get("classifiers") or {}).get(natives_key)
            if info and info.get("url"):
                exclude = tuple((lib.get("extract") or {}).get("exclude") or ["META-INF/"])
                result.append(
                    Task(
                        info["url"],
                        data_dir / "libraries" / info["path"],
                        info.get("sha1"),
                        info.get("size"),
                        extract=True,
                        exclude=exclude,
                    )
                )
        source = versions.library_source(lib)
        if source:
            url, path, sha1, size = source
            result.append(Task(url, data_dir / "libraries" / path, sha1, size))
        return result

    def _asset_tasks(self, vjson: dict) -> list[Task]:
        data_dir = self.data_dir
        asset_index = vjson.get("assetIndex")
        if not asset_index:
            return []
        index_path = data_dir / "assets" / "indexes" / f"{asset_index['id']}.json"
        if not net.file_is_valid(index_path, asset_index.get("sha1"), asset_index.get("size")):
            self.progress.set_stage("Скачиваю индекс ресурсов", str(asset_index["id"]))
            self._check_cancel()
            net.download(
                asset_index["url"], index_path, asset_index.get("sha1"), asset_index.get("size"), cancel=self.cancel
            )
        index = json.loads(index_path.read_text(encoding="utf-8"))
        objects = index.get("objects") or {}
        self.progress.set_stage("Ресурсы игры", f"{len(objects)} объектов")
        tasks: list[Task] = []
        for obj in objects.values():
            digest = obj.get("hash")
            if not digest:
                continue
            sub = digest[:2]
            tasks.append(
                Task(
                    f"https://resources.download.minecraft.net/{sub}/{digest}",
                    data_dir / "assets" / "objects" / sub / digest,
                    digest,
                    obj.get("size"),
                )
            )
        return tasks

    @staticmethod
    def _dedupe(tasks: list[Task]) -> list[Task]:
        """Разные имена ассетов часто ссылаются на один файл-хеш:
        два потока на один путь ловят WinError 32, поэтому дедуплицируем."""
        unique: dict[str, Task] = {}
        for task in tasks:
            key = str(task.dest).lower()
            if key not in unique:
                unique[key] = task
        return list(unique.values())

    # ------------------------------------------------------------ установка

    def install(self, game: str, loader: str = "vanilla", repair: bool = False) -> dict:
        """Ставит ваниль и, если выбрано, слой загрузчика поверх неё.

        repair=True проверяет и докачивает файлы уже установленной версии,
        не запуская заново установщик Forge/NeoForge.
        """
        progress = self.progress
        progress.reset()
        self._check_cancel()
        existing = loaders.find_installed(self.data_dir, game, loader) if repair else ""

        plan = loaders.plan(self.data_dir, game, loader)
        progress.set_stage(f"Minecraft {game}", "готовлю список файлов")
        vjson, tasks, natives_dir = self._collect(game)
        required_java = versions.java_requirement_from(vjson)

        if plan["kind"] == "profile":
            extra_tasks, profile = self._collect_profile(plan)
            tasks = self._dedupe(tasks + extra_tasks)
            self._write_profile(plan, profile)

        pending, total_bytes, done_bytes, done_files = self._split(tasks)
        progress.set_total(total_bytes, len(pending), done_bytes, done_files)
        progress.set_stage(
            f"Скачиваю Minecraft {game}" if pending else "Проверяю целостность файлов",
            f"{len(pending)} файлов · {human_size(total_bytes)}",
        )
        self.log(f"{game}: {len(pending)} файлов к загрузке, {human_size(total_bytes)}")
        self._run_downloads(pending)

        self._check_cancel()
        progress.set_stage("Распаковываю нативные библиотеки", "natives")
        self._extract_natives([task for task in tasks if task.extract], natives_dir)
        self._write_marker(f"versions/{game}", self._dir_size(self.data_dir / "versions" / game))

        final_id = game
        if plan["kind"] == "installer":
            final_id = existing or self._run_installer(plan, required_java)
        elif plan["kind"] == "profile":
            final_id = plan["version_id"]
            self._write_marker(f"versions/{final_id}", self._dir_size(self.data_dir / "versions" / final_id))

        self._check_cancel()
        progress.finish()
        size = self.summary_size(final_id)
        self.log(f"{final_id} установлена · {human_size(size)}")
        return {"installed_at": time.time(), "version_id": final_id, "size": size, "loader": loader}

    def _collect_profile(self, plan: dict) -> tuple[list[Task], dict]:
        data_dir = self.data_dir
        self.progress.set_stage(f"Запрашиваю профиль {plan['label']}", "meta.fabricmc.net")
        self._check_cancel()
        profile = net.fetch_json(plan["profile_url"])
        tasks: list[Task] = []
        for lib in profile.get("libraries") or []:
            if not versions.rules_allow(lib.get("rules")):
                continue
            tasks.extend(self._library_tasks(lib))
        return tasks, profile

    def _write_profile(self, plan: dict, profile: dict) -> None:
        target = self.data_dir / "versions" / plan["version_id"]
        target.mkdir(parents=True, exist_ok=True)
        profile = dict(profile)
        profile["id"] = plan["version_id"]
        profile["inheritsFrom"] = plan["game"]
        profile["type"] = "release"
        (target / f"{plan['version_id']}.json").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _run_installer(self, plan: dict, required_java: int) -> str:
        """Forge и NeoForge ставятся своим официальным установщиком."""
        progress = self.progress
        installers_dir = self.data_dir / "installers"
        installers_dir.mkdir(parents=True, exist_ok=True)
        jar = installers_dir / Path(plan["installer_url"]).name
        progress.set_indeterminate(True, f"{plan['label']}: запускаю официальный установщик")
        progress.set_stage(f"Устанавливаю {plan['label']}", "это может занять пару минут")
        self._check_cancel()

        if not jar.is_file():
            self.log(f"Скачиваю установщик {plan['label']}…")
            net.download(plan["installer_url"], jar, cancel=self.cancel)

        self._ensure_launcher_profile()

        javaw, major = java_core.ensure_java(
            required_java, log=self.log, cancel=self.cancel, on_progress=progress.add
        )
        self.log(f"Установщик {plan['label']} на Java {major}")
        self._check_cancel()

        command = [str(javaw), "-jar", str(jar), "--installClient", str(self.data_dir)]
        try:
            result = subprocess.run(
                command,
                cwd=str(self.data_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=1800,
                creationflags=java_core.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise InstallError(f"Установщик {plan['label']} не запустился: {exc}") from exc

        tail = ((result.stdout or "") + (result.stderr or "")).strip().splitlines()
        for line in tail[-6:]:
            self.log(line[:160])

        folder = loaders.find_installed(self.data_dir, plan["game"], plan["loader"])
        if not folder:
            detail = tail[-1][:160] if tail else f"код {result.returncode}"
            raise InstallError(f"{plan['label']} не установился: {detail}")

        self.log(f"{plan['label']} установлен в versions/{folder}")
        self._write_marker(
            f"versions/{folder}", self._dir_size(self.data_dir / "versions" / folder)
        )
        progress.set_indeterminate(False)
        return folder

    def _run_downloads(self, pending: list[Task]) -> None:
        if not pending:
            return
        progress = self.progress

        def run(task: Task) -> None:
            if self.cancel.is_set():
                raise net.DownloadCancelled()
            net.download(
                task.url,
                task.dest,
                task.sha1,
                task.size,
                on_bytes=progress.add,
                cancel=self.cancel,
            )
            progress.complete_file()

        pool = ThreadPoolExecutor(max_workers=WORKERS)
        futures = [pool.submit(run, task) for task in pending]
        try:
            done, not_done = wait(futures, return_when=FIRST_EXCEPTION)
            for future in done:
                error = future.exception()
                if error is None:
                    continue
                for other in not_done:
                    other.cancel()
                raise error
            wait(futures)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def _split(self, tasks: list[Task]) -> tuple[list[Task], int, int, int]:
        pending: list[Task] = []
        done_bytes = 0
        done_files = 0
        total_bytes = 0
        for task in tasks:
            if task.sha1 and net.file_is_valid(task.dest, task.sha1, task.size):
                done_bytes += task.size or task.dest.stat().st_size
                done_files += 1
            else:
                pending.append(task)
                total_bytes += task.size or 0
        return pending, total_bytes, done_bytes, done_files

    def _extract_natives(self, tasks: list[Task], natives_dir: Path) -> None:
        if not tasks:
            return
        if natives_dir.exists():
            shutil.rmtree(natives_dir, ignore_errors=True)
        natives_dir.mkdir(parents=True, exist_ok=True)
        for task in tasks:
            if not task.dest.is_file():
                continue
            try:
                with zipfile.ZipFile(task.dest) as archive:
                    for info in archive.infolist():
                        if info.is_dir():
                            continue
                        name = info.filename.replace("\\", "/")
                        if any(name.startswith(prefix) for prefix in task.exclude):
                            continue
                        target = natives_dir / Path(name).name
                        with archive.open(info) as source, open(target, "wb") as handle:
                            shutil.copyfileobj(source, handle)
            except (OSError, zipfile.BadZipFile) as exc:
                self.log(f"Нативы {task.dest.name}: {exc}")

    # -------------------------------------------------------------- утилиты

    def _ensure_launcher_profile(self) -> None:
        """Старые установщики Forge требуют launcher_profiles.json в папке игры."""
        profile = self.data_dir / "launcher_profiles.json"
        if profile.is_file():
            return
        payload = {
            "profiles": {},
            "settings": {"enableSnapshots": False, "keepLauncherOpen": False, "showMenu": False},
            "version": 3,
        }
        try:
            profile.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _write_marker(self, relative: str, size: int) -> None:
        marker = self.data_dir / relative / ".vleyz-installed"
        try:
            marker.write_text(str(size), encoding="utf-8")
        except OSError:
            pass

    def _dir_size(self, root: Path) -> int:
        total = 0
        if not root.is_dir():
            return 0
        for path in root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
        return total

    def summary_size(self, version_id: str) -> int:
        """Полный вес установки: версия + её ванильная основа."""
        total = self._dir_size(self.data_dir / "versions" / version_id)
        try:
            vjson = versions.version_json(self.data_dir, version_id)
        except (FileNotFoundError, ValueError):
            return total
        parent = vjson.get("inheritsFrom")
        if parent:
            total += self._dir_size(self.data_dir / "versions" / parent)
        libraries = self._dir_size(self.data_dir / "libraries")
        assets = self._dir_size(self.data_dir / "assets")
        return total + libraries + assets


def uninstall(data_dir: Path, version_id: str) -> None:
    """Удаляет папку версии. Библиотеки и ассеты общие, их не трогаем."""
    version_dir = Path(data_dir) / "versions" / version_id
    if not version_dir.is_dir():
        return
    try:
        shutil.rmtree(version_dir)
    except OSError:
        pass


def remove_download_cache(data_dir: Path, keep_installers: bool = True) -> None:
    """Чистка недокачанных .part-файлов и скачанных установщиков."""
    for path in Path(data_dir).rglob("*.part"):
        try:
            path.unlink()
        except OSError:
            continue
    if keep_installers:
        return
    for path in (Path(data_dir) / "installers").glob("*.jar"):
        try:
            path.unlink()
        except OSError:
            continue
