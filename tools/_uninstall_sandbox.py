"""Проверка сгенерированного деинсталлятора в песочнице (без лаунчера)."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    spec = importlib.util.spec_from_file_location(
        "setup_under_test", ROOT / "tools" / "installer" / "setup.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render(module, folder: Path, temp: Path) -> str:
    return module.UNINSTALL_TEMPLATE.format(
        temp=str(temp),
        exe="VleyzTest.exe",
        name="VleyzTest",
        folder=str(folder),
        marker=module.INSTALL_MARKER,
        datamarker=module.DATA_MARKER,
        datafolder="VleyzTestData",
        portable=module.PORTABLE_MARKER,
        uninstaller=module.UNINSTALL_NAME,
    )


def run_case(module, title: str, prepare, stdin_text: str = "",
             folder_name: str = "install/VleyzLauncher", env_of=None) -> None:
    with tempfile.TemporaryDirectory() as base:
        base = Path(base)
        temp = base / "temp"
        folder = base / folder_name
        temp.mkdir()
        folder.mkdir(parents=True, exist_ok=True)
        prepare(folder)
        script = temp / "vleyz-uninstall.cmd"
        script.write_text(render(module, folder, temp), encoding="utf-8")
        env = dict(os.environ, **(env_of(folder) if env_of else {}))
        done = subprocess.run(
            ["cmd", "/c", str(script)],
            input=(stdin_text or "\n").encode("utf-8"),
            capture_output=True,
            timeout=60,
            env=env,
        )
        print(f"--- {title} (rc={done.returncode})")
        print(done.stdout.decode("utf-8", "replace").strip())
        left = sorted(p.name for p in folder.iterdir()) if folder.exists() else []
        print("осталось:", left)
        print("папка есть:", folder.exists())


def main() -> int:
    module = load_module()

    def foreign(folder: Path) -> None:
        (folder / "чужой.txt").write_text("x", encoding="utf-8")

    def normal(folder: Path) -> None:
        (folder / module.INSTALL_MARKER).write_text("{}", encoding="utf-8")
        (folder / "VleyzTest.exe").write_text("x", encoding="utf-8")
        (folder / "config.json").write_text("{}", encoding="utf-8")
        (folder / "чужой.txt").write_text("x", encoding="utf-8")

    def clean(folder: Path) -> None:
        (folder / module.INSTALL_MARKER).write_text("{}", encoding="utf-8")
        (folder / "VleyzTest.exe").write_text("x", encoding="utf-8")

    def with_data(folder: Path) -> None:
        (folder / module.INSTALL_MARKER).write_text("{}", encoding="utf-8")
        (folder / "VleyzTest.exe").write_text("x", encoding="utf-8")
        (folder / "minecraft").mkdir()
        (folder / "minecraft" / module.DATA_MARKER).write_text("{}", encoding="utf-8")
        (folder / "minecraft" / "saves").mkdir()
        (folder / "logs").mkdir()
        (folder / "logs" / "launcher.log").write_text("x", encoding="utf-8")
        jdk = folder / "java" / "bin"
        jdk.mkdir(parents=True)
        (jdk / "java.exe").write_text("x", encoding="utf-8")
        (folder / "чужой.txt").write_text("x", encoding="utf-8")

    run_case(module, "без маркера — ничего не трогать", foreign)
    run_case(module, "с маркером, есть чужой файл, N", normal, "N\n")
    run_case(module, "с маркером, папка пустая после удаления", clean, "N\n")
    run_case(
        module, "Y: minecraft/logs удаляются, чужой JDK и файл остаются", with_data, "Y\n",
        env_of=lambda folder: {"APPDATA": str(folder.parents[1] / "appdata")},
    )
    run_case(
        module, "папка = USERPROFILE с маркером — отказ", normal, "Y\n",
        folder_name="home", env_of=lambda folder: {"USERPROFILE": str(folder)},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
