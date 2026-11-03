"""Проверка запуска: структура команды и, по флагу --run, короткий прогон игры."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vleyzlauncher.core import java as java_core
from vleyzlauncher.core import launch as launch_core
from vleyzlauncher.core import loaders, paths, versions


def check(game: str, loader: str = "vanilla") -> int:
    cfg = paths.load_config()
    data_dir = paths.data_dir(cfg)
    version_id = loaders.find_installed(data_dir, game, loader)
    if not version_id:
        print(f"{game}/{loader}: не установлена")
        return 1
    needed = versions.java_requirement(data_dir, version_id)
    found = java_core.find_java(needed, cfg.get("java_path", ""))
    if not found:
        print(f"{version_id}: нужна Java {needed}, подходящей нет")
        return 1

    command = launch_core.build_command(data_dir, version_id, "TestPlayer", 2048, found[0])
    problems: list[str] = []

    leftovers = [arg for arg in command if "${" in arg]
    if leftovers:
        problems.append(f"не подставлены плейсхолдеры: {leftovers[:3]}")

    classpath: list[str] = []
    if "-cp" in command:
        classpath = command[command.index("-cp") + 1].split(";")
    else:
        problems.append("нет -cp в команде")
    missing = [path for path in classpath if not Path(path).is_file()]
    if missing:
        problems.append(f"нет файлов в classpath: {missing[:3]}")

    vjson = versions.resolved_json(data_dir, version_id)
    natives = launch_core.natives_dir(data_dir, vjson, version_id)
    dlls = list(natives.glob("*.dll")) if natives.is_dir() else []
    if not dlls:
        problems.append("нет нативных библиотек")

    print(f"{version_id}: java={found[0].name} {found[1]}, аргументов={len(command)}, "
          f"classpath={len(classpath)} файлов, нативов={len(dlls)}")
    for problem in problems:
        print("  ПРОБЛЕМА:", problem)
    return 1 if problems else 0


def smoke_run(game: str, loader: str = "vanilla", seconds: int = 25) -> int:
    cfg = paths.load_config()
    data_dir = paths.data_dir(cfg)
    version_id = loaders.find_installed(data_dir, game, loader)
    if not version_id:
        return 1
    needed = versions.java_requirement(data_dir, version_id)
    found = java_core.find_java(needed, cfg.get("java_path", ""))
    if not found:
        return 1
    command = launch_core.build_command(data_dir, version_id, "TestPlayer", 2048, found[0])
    console_java = found[0].with_name("java.exe")
    if console_java.is_file():
        command[0] = str(console_java)

    print(f"\nЗапуск {version_id} на {seconds} секунд…")
    process = subprocess.Popen(
        command, cwd=str(data_dir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    deadline = time.time() + seconds
    lines: list[str] = []
    while time.time() < deadline:
        line = process.stdout.readline()
        if not line:
            if process.poll() is not None:
                break
            time.sleep(0.2)
            continue
        lines.append(line.rstrip())
        print("   ", line.rstrip()[:160], flush=True)
        if len(lines) > 200:
            break
    alive = process.poll() is None
    if alive:
        process.kill()
    process.wait(timeout=10)
    print(f"  процесс {'работал и был остановлен' if alive else 'завершился сам'} "
          f"(код {process.returncode}), строк лога: {len(lines)}")
    errors = [line for line in lines if "ClassNotFound" in line or "NoClassDefFound" in line
              or "Could not find or load" in line or "Exception" in line]
    for error in errors[:5]:
        print("  ОШИБКА:", error)
    return 1 if errors else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    do_run = "--run" in args
    rest = [arg for arg in args if not arg.startswith("--")]
    game = rest[0] if rest else "1.16.5"
    loader = rest[1] if len(rest) > 1 else "vanilla"
    failed = check(game, loader)
    if do_run:
        failed += smoke_run(game, loader)
    sys.exit(1 if failed else 0)
