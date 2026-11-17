"""Проверка запуска 1.20.1 Forge с исправленным выбором Java."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vleyzlauncher.core import java as java_core
from vleyzlauncher.core import launch as launch_core
from vleyzlauncher.core import versions as version_core

DATA = Path(r"C:\Users\Admin\AppData\Local\Programs\VleyzLauncher\minecraft")
VERSION = "1.20.1-forge-47.4.28"

needed = version_core.java_requirement(DATA, VERSION)
found = java_core.find_java(needed)
print("нужна Java:", needed, "| найдена:", found)
if not found:
    raise SystemExit("Java не найдена")

game_dir = DATA / "instances" / VERSION
process, log_path = launch_core.launch(
    DATA, VERSION, "vleyzz", 2048, found[0], None, None, "", game_dir
)
print("процесс:", process.pid, "лог:", log_path)
for _ in range(10):
    time.sleep(3)
    code = process.poll()
    if code is not None:
        print("игра завершилась, код:", code)
        print(launch_core.read_log_tail(log_path, 8))
        raise SystemExit(1)
    print("жива", int(time.time()))
print("игра работает — завершаю проверку")
process.terminate()
