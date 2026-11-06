"""Установщик: копирует лаунчер, делает ярлыки, кладёт файл удаления.

Собирается через VleyzLauncherSetup.spec. Для проверки умеет работать без интерфейса:
    VleyzLauncher-Setup.exe --silent --dir <папка> --no-shortcuts
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "Vleyz Launcher"
EXE_NAME = "VleyzLauncher.exe"
UNINSTALL_NAME = "Удалить Vleyz Launcher.cmd"
INSTALL_MARKER = ".vleyz-install"
DATA_MARKER = ".vleyzlauncher"
DATA_FOLDER = "VleyzLauncher"
CREATE_NO_WINDOW = 0x08000000

DEFAULT_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / "VleyzLauncher"

UNINSTALL_TEMPLATE = """@echo off
chcp 65001 >nul
if /i "%~dp0"=="{temp}\\" goto run
copy /y "%~f0" "{temp}\\vleyz-uninstall.cmd" >nul
start "" "{temp}\\vleyz-uninstall.cmd"
exit /b
:run
taskkill /f /im {exe} >nul 2>&1
del "%USERPROFILE%\\Desktop\\{name}.lnk" >nul 2>&1
del "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\{name}.lnk" >nul 2>&1
if not exist "{folder}\\{marker}" goto foreign
del "{folder}\\{exe}" >nul 2>&1
del "{folder}\\{marker}" >nul 2>&1
del "{folder}\\{uninstaller}" >nul 2>&1
rmdir "{folder}" 2>nul
echo Лаунчер удалён. Чужие файлы в папке установки не тронуты.
if not exist "%APPDATA%\\{datafolder}\\{datamarker}" goto end
choice /c YN /n /m "Удалить также скачанные версии игры, миры и настройки? [Y/N] "
if errorlevel 2 goto end
rmdir /s /q "%APPDATA%\\{datafolder}\\minecraft" 2>nul
rmdir /s /q "%APPDATA%\\{datafolder}\\java" 2>nul
rmdir /s /q "%APPDATA%\\{datafolder}\\cache" 2>nul
rmdir /s /q "%APPDATA%\\{datafolder}\\logs" 2>nul
del "%APPDATA%\\{datafolder}\\config.json" >nul 2>&1
del "%APPDATA%\\{datafolder}\\{datamarker}" >nul 2>&1
rmdir "%APPDATA%\\{datafolder}" 2>nul
echo Данные лаунчера удалены.
goto end
:foreign
echo Папка "{folder}" не помечена как папка лаунчера.
echo Удалены только ярлыки, файлы в папке не тронуты.
:end
(goto) 2>nul & del "%~f0" >nul 2>&1
"""


def payload_path() -> Path:
    """Собранный лаунчер, вложенный в установщик."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    candidates = [
        base / "payload" / EXE_NAME,
        base / EXE_NAME,
        base.parent / "dist" / EXE_NAME,
        base.parent.parent / "dist" / EXE_NAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("не найден VleyzLauncher.exe — пересоберите установщик")


def make_shortcut(link: Path, target: Path) -> bool:
    script = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
        f"$s.TargetPath='{target}';"
        f"$s.WorkingDirectory='{target.parent}';"
        f"$s.IconLocation='{target},0';"
        f"$s.Description='{APP_NAME}';"
        f"$s.Save()"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, creationflags=CREATE_NO_WINDOW,
    )
    return result.returncode == 0 and link.is_file()


def install(target_dir: Path, desktop: bool = True, start_menu: bool = True) -> Path:
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / EXE_NAME
    shutil.copy2(payload_path(), target)

    if desktop:
        make_shortcut(Path(os.environ["USERPROFILE"]) / "Desktop" / f"{APP_NAME}.lnk", target)
    if start_menu:
        menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        menu.mkdir(parents=True, exist_ok=True)
        make_shortcut(menu / f"{APP_NAME}.lnk", target)

    (target_dir / INSTALL_MARKER).write_text(
        json.dumps({"kind": "install", "app": DATA_FOLDER}, ensure_ascii=False),
        encoding="utf-8",
    )

    uninstall = target_dir / UNINSTALL_NAME
    uninstall.write_text(
        UNINSTALL_TEMPLATE.format(
            temp=os.environ.get("TEMP", "."),
            exe=EXE_NAME,
            name=APP_NAME,
            folder=target_dir,
            marker=INSTALL_MARKER,
            datamarker=DATA_MARKER,
            datafolder=DATA_FOLDER,
            uninstaller=UNINSTALL_NAME,
        ),
        encoding="utf-8",
    )
    return target


def launch(target: Path) -> None:
    subprocess.Popen([str(target)], cwd=str(target.parent), close_fds=True)


class SetupWindow:
    BG = "#0B0C11"
    PANEL = "#151823"
    TEXT = "#EBEDF3"
    MUTED = "#969CA9"
    ACCENT = "#7C8CFA"
    BORDER = "#242833"
    FIELD = "#0F1118"

    def __init__(self) -> None:
        import tkinter as tk

        self.tk = tk
        self.root = tk.Tk()
        self.root.title(f"Установка {APP_NAME}")
        self.root.configure(bg=self.BG)
        self.root.geometry("620x380")
        self.root.resizable(False, False)
        self.installed: Path | None = None

        tk.Label(self.root, text=f"Установка {APP_NAME}", bg=self.BG, fg=self.TEXT,
                 font=("Segoe UI Semibold", 17), anchor="w").place(x=28, y=24)
        tk.Label(self.root,
                 text="Лаунчер встанет в папку программ. Игру скачаете уже в нём — в пару кликов.",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9), anchor="w").place(x=28, y=58)

        tk.Label(self.root, text="Куда установить", bg=self.BG, fg=self.MUTED,
                 font=("Segoe UI", 9), anchor="w").place(x=28, y=104)
        self.dir_var = tk.StringVar(value=str(DEFAULT_DIR))
        entry = tk.Entry(self.root, textvariable=self.dir_var, bg=self.FIELD, fg=self.TEXT,
                         insertbackground=self.ACCENT, relief="flat", font=("Segoe UI", 9))
        entry.place(x=28, y=128, width=470, height=30)
        tk.Button(self.root, text="Обзор…", command=self._pick_dir, bg=self.PANEL, fg=self.TEXT,
                  activebackground=self.BORDER, relief="flat", font=("Segoe UI", 9)).place(
            x=508, y=128, width=84, height=30)

        self.desktop_var = tk.BooleanVar(value=True)
        self.menu_var = tk.BooleanVar(value=True)
        tk.Checkbutton(self.root, text="Ярлык на рабочем столе", variable=self.desktop_var,
                       bg=self.BG, fg=self.TEXT, selectcolor=self.PANEL, activebackground=self.BG,
                       activeforeground=self.TEXT, font=("Segoe UI", 9), anchor="w").place(x=28, y=176)
        tk.Checkbutton(self.root, text="Ярлык в меню «Пуск»", variable=self.menu_var,
                       bg=self.BG, fg=self.TEXT, selectcolor=self.PANEL, activebackground=self.BG,
                       activeforeground=self.TEXT, font=("Segoe UI", 9), anchor="w").place(x=28, y=202)

        self.status = tk.Label(self.root, text="Готов к установке", bg=self.BG, fg=self.MUTED,
                               font=("Segoe UI", 9), anchor="w")
        self.status.place(x=28, y=248)

        self.primary = tk.Button(self.root, text="Установить", command=self._install,
                                 bg=self.ACCENT, fg="#0A0D18", activebackground="#5A67E8",
                                 relief="flat", font=("Segoe UI Semibold", 10))
        self.primary.place(x=28, y=288, width=180, height=40)
        self.secondary = tk.Button(self.root, text="Закрыть", command=self.root.destroy,
                                   bg=self.PANEL, fg=self.TEXT, activebackground=self.BORDER,
                                   relief="flat", font=("Segoe UI", 10))
        self.secondary.place(x=220, y=288, width=140, height=40)

    def _pick_dir(self) -> None:
        from tkinter import filedialog

        chosen = filedialog.askdirectory(title="Куда установить лаунчер")
        if chosen:
            self.dir_var.set(chosen)

    def _install(self) -> None:
        target_dir = Path(self.dir_var.get().strip() or DEFAULT_DIR)
        try:
            self.status.configure(text="Копирую файлы…", fg=self.TEXT)
            self.root.update_idletasks()
            target = install(target_dir, self.desktop_var.get(), self.menu_var.get())
        except Exception as exc:  # noqa: BLE001 - показываем пользователю
            self.status.configure(text=f"Ошибка: {exc}", fg="#F87171")
            return
        self.installed = target
        self.status.configure(text=f"Готово: {target}", fg="#7C8CFA")
        self.primary.configure(text="Запустить лаунчер", bg="#4ADE80", fg="#06220F",
                               activebackground="#22B364", command=self._launch)
        self.secondary.configure(text="Готово", command=self.root.destroy)

    def _launch(self) -> None:
        if self.installed is not None:
            launch(self.installed)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="VleyzLauncher-Setup")
    parser.add_argument("--silent", action="store_true", help="установить без интерфейса")
    parser.add_argument("--dir", default="", help="папка установки")
    parser.add_argument("--no-shortcuts", action="store_true", help="не создавать ярлыки")
    parser.add_argument("--run", action="store_true", help="запустить лаунчер после установки")
    args = parser.parse_args(argv)

    if args.silent:
        target = install(Path(args.dir or DEFAULT_DIR), not args.no_shortcuts, not args.no_shortcuts)
        if sys.stdout is not None:
            print(target)
        if args.run:
            launch(target)
        return 0

    SetupWindow().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
