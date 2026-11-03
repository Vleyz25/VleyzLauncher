"""Проверка сборки команды запуска без запуска игры."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vleyzlauncher.core import java, launch as launch_core, paths, versions


def main(version_ids: list[str]) -> int:
    cfg = paths.load_config()
    data_dir = paths.data_dir(cfg)
    failed = 0
    for version_id in version_ids:
        try:
            vjson = versions.resolved_json(data_dir, version_id)
        except (FileNotFoundError, ValueError) as exc:
            print(f"{version_id}: {exc}")
            failed += 1
            continue
        needed = versions.java_requirement_from(vjson)
        found = java.find_java(needed, cfg.get("java_path", ""))
        if not found:
            print(f"{version_id}: нужна Java {needed}, подходящей нет")
            failed += 1
            continue
        command = launch_core.build_command(data_dir, version_id, "TestPlayer", 2048, found[0])
        leftovers = [arg for arg in command if "${" in arg]
        classpath: list[str] = []
        if "-cp" in command:
            classpath = command[command.index("-cp") + 1].split(";")
        missing = [path for path in classpath if not Path(path).is_file()]
        natives = launch_core.natives_dir(data_dir, vjson, version_id)
        dlls = list(natives.glob("*.dll")) if natives.is_dir() else []
        problems = []
        if leftovers:
            problems.append(f"не подставлено: {leftovers[:2]}")
        if missing:
            problems.append(f"нет в classpath: {missing[:2]}")
        print(
            f"{version_id}: java={found[0].name} {found[1]}, аргументов={len(command)}, "
            f"classpath={len(classpath)}, нативов={len(dlls)}"
        )
        if problems:
            for problem in problems:
                print("  ПРОБЛЕМА:", problem)
            failed += 1
        else:
            print("  структура в порядке")
    return 1 if failed else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(main(args or ["1.16.5"]))
