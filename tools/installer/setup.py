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
FOLDER_NAME = "VleyzLauncher"
UNINSTALL_NAME = "Удалить Vleyz Launcher.cmd"
INSTALL_MARKER = ".vleyz-install"
DATA_MARKER = ".vleyzlauncher"
PORTABLE_MARKER = ".vleyz-portable"
DATA_FOLDER = "VleyzLauncher"
CREATE_NO_WINDOW = 0x08000000

DEFAULT_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs"


def resolve_target_dir(chosen: Path) -> Path:
    """Папка лаунчера внутри выбранного места.

    Если человек выбрал уже готовую папку лаунчера (обновление поверх) —
    оставляем её как есть, иначе создаём подпапку VleyzLauncher.
    """
    chosen = Path(os.path.abspath(Path(chosen)))
    if chosen.name.lower() == FOLDER_NAME.lower():
        return chosen
    if (chosen / INSTALL_MARKER).is_file():
        return chosen
    return chosen / FOLDER_NAME


def unsafe_install_reason(folder: Path) -> str:
    """Почему в эту папку нельзя ставить лаунчер (пустая строка — можно).

    Деинсталлятор не должен получить в руки системный каталог: даже с
    маркером и без рекурсивного удаления это слишком дорогая ошибка.
    """
    folder = Path(os.path.abspath(Path(folder)))
    drive, tail = os.path.splitdrive(str(folder))
    if not tail.strip("\\/"):
        return "корень диска"
    guarded = [
        os.environ.get("USERPROFILE", ""),
        os.environ.get("LOCALAPPDATA", ""),
        os.environ.get("APPDATA", ""),
        os.environ.get("ProgramData", ""),
        os.environ.get("ProgramFiles", ""),
        os.environ.get("ProgramFiles(x86)", ""),
        os.environ.get("SystemRoot", ""),
        os.environ.get("PUBLIC", ""),
    ]
    for base in guarded:
        if base and os.path.normcase(os.path.abspath(base)) == os.path.normcase(str(folder)):
            return f"системная папка {base}"
    return ""


def normalize_temp(value: str) -> str:
    """Путь к TEMP без хвостового слэша: иначе .cmd копирует сам себя вечно."""
    cleaned = (value or ".").strip().rstrip("\\/")
    return cleaned or "."

UNINSTALL_TEMPLATE = """@echo off
chcp 65001 >nul
setlocal enableextensions
set "VL_DIR={folder}"
set "VL_TEMP={temp}"
if /i "%~dp0"=="%VL_TEMP%\\" goto run
copy /y "%~f0" "%VL_TEMP%\\vleyz-uninstall.cmd" >nul 2>&1
start "" "%VL_TEMP%\\vleyz-uninstall.cmd"
exit /b
:run
taskkill /f /im {exe} >nul 2>&1
del "%USERPROFILE%\\Desktop\\{name}.lnk" >nul 2>&1
del "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\{name}.lnk" >nul 2>&1
if not exist "%VL_DIR%\\{marker}" goto foreign
set "VL_CHECK=%VL_DIR%"
if "%VL_CHECK:~-1%"=="\\" set "VL_CHECK=%VL_CHECK:~0,-1%"
if "%VL_CHECK:~-1%"==":" goto unsafe
if /i "%VL_CHECK%"=="%USERPROFILE%" goto unsafe
if /i "%VL_CHECK%"=="%LOCALAPPDATA%" goto unsafe
if /i "%VL_CHECK%"=="%APPDATA%" goto unsafe
if /i "%VL_CHECK%"=="%SystemDrive%" goto unsafe
if /i "%VL_CHECK%"=="%SystemRoot%" goto unsafe
if /i "%VL_CHECK%"=="%ProgramFiles%" goto unsafe
if /i "%VL_CHECK%"=="%ProgramFiles(x86)%" goto unsafe
if /i "%VL_CHECK%"=="%ProgramData%" goto unsafe
if /i "%VL_CHECK%"=="%PUBLIC%" goto unsafe
if /i "%VL_CHECK%"=="%USERPROFILE%\\Desktop" goto unsafe
if /i "%VL_CHECK%"=="%USERPROFILE%\\Documents" goto unsafe
if /i "%VL_CHECK%"=="%USERPROFILE%\\Downloads" goto unsafe
choice /c YN /n /m "Удалить также версии игры, миры и Java? [Y - удалить, N - оставить] "
if errorlevel 2 goto files
if exist "%VL_DIR%\\minecraft\\{datamarker}" rmdir /s /q "%VL_DIR%\\minecraft" 2>nul
if not exist "%VL_DIR%\\java\\bin\\java.exe" rmdir /s /q "%VL_DIR%\\java" 2>nul
rmdir /s /q "%VL_DIR%\\logs" 2>nul
if "%APPDATA%"=="" goto files
if exist "%APPDATA%\\{datafolder}\\minecraft\\{datamarker}" rmdir /s /q "%APPDATA%\\{datafolder}\\minecraft" 2>nul
if not exist "%APPDATA%\\{datafolder}\\java\\bin\\java.exe" rmdir /s /q "%APPDATA%\\{datafolder}\\java" 2>nul
rmdir /s /q "%APPDATA%\\{datafolder}\\logs" 2>nul
rmdir /s /q "%APPDATA%\\{datafolder}\\cache" 2>nul
del "%APPDATA%\\{datafolder}\\config.json" >nul 2>&1
del "%APPDATA%\\{datafolder}\\{datamarker}" >nul 2>&1
rmdir "%APPDATA%\\{datafolder}" 2>nul
:files
del "%VL_DIR%\\{exe}" >nul 2>&1
del "%VL_DIR%\\{marker}" >nul 2>&1
del "%VL_DIR%\\{portable}" >nul 2>&1
del "%VL_DIR%\\{uninstaller}" >nul 2>&1
del "%VL_DIR%\\config.json" >nul 2>&1
rmdir "%VL_DIR%" 2>nul
if exist "%VL_DIR%" goto leftover
echo Лаунчер удалён. Чужие файлы в папке не тронуты.
goto end
:leftover
echo Лаунчер удалён. В папке остались чужие файлы, поэтому она не удалена:
echo %VL_DIR%
goto end
:unsafe
echo Папка "%VL_DIR%" похожа на системную. Удаление отменено.
echo Удалены только ярлыки, файлы не тронуты.
goto end
:foreign
echo Папка "%VL_DIR%" не помечена как папка лаунчера.
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


def known_folder(name: str) -> Path:
    """Настоящий путь «Рабочего стола» или «Документов».

    Учитывает перенос папок в OneDrive: explorer пишет их в реестр, а
    %USERPROFILE%\\Desktop в таком случае может быть пустой заглушкой.
    """
    try:
        import winreg

        key_path = (
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        )
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, name)
        path = Path(os.path.expandvars(str(value)))
        if str(value).strip() and path.is_dir():
            return path
    except (OSError, ValueError):
        pass
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / name


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


DATA_SUBFOLDERS = ("minecraft", "java", "logs")


def ensure_layout(target_dir: Path) -> None:
    """Одна папка со всем: exe, minecraft, java, logs.

    Папки создаются сразу, чтобы человек видел, где будут лежать версии игры,
    скачанная Java и логи. `minecraft` помечается маркером данных — иначе
    деинсталлятор не станет его трогать.
    """
    target_dir = Path(target_dir)
    for name in DATA_SUBFOLDERS:
        (target_dir / name).mkdir(parents=True, exist_ok=True)
    data_marker = target_dir / "minecraft" / DATA_MARKER
    if not data_marker.is_file():
        data_marker.write_text(
            json.dumps({"kind": "data", "app": DATA_FOLDER}, ensure_ascii=False),
            encoding="utf-8",
        )


def install(target_dir: Path, desktop: bool = True, start_menu: bool = True) -> Path:
    target_dir = resolve_target_dir(Path(target_dir))
    reason = unsafe_install_reason(target_dir)
    if reason:
        raise ValueError(f"нельзя ставить лаунчер в {target_dir}: {reason}")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / EXE_NAME
    shutil.copy2(payload_path(), target)
    ensure_layout(target_dir)

    if desktop:
        shortcut = known_folder("Desktop") / f"{APP_NAME}.lnk"
        shortcut.parent.mkdir(parents=True, exist_ok=True)
        make_shortcut(shortcut, target)
    if start_menu:
        menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        menu.mkdir(parents=True, exist_ok=True)
        make_shortcut(menu / f"{APP_NAME}.lnk", target)

    (target_dir / INSTALL_MARKER).write_text(
        json.dumps({"kind": "install", "app": DATA_FOLDER}, ensure_ascii=False),
        encoding="utf-8",
    )
    (target_dir / PORTABLE_MARKER).write_text(
        json.dumps({"kind": "portable", "data": "minecraft", "java": "java"},
                   ensure_ascii=False),
        encoding="utf-8",
    )

    uninstall = target_dir / UNINSTALL_NAME
    uninstall.write_text(
        UNINSTALL_TEMPLATE.format(
            temp=normalize_temp(os.environ.get("TEMP", ".")),
            exe=EXE_NAME,
            name=APP_NAME,
            folder=target_dir,
            marker=INSTALL_MARKER,
            datamarker=DATA_MARKER,
            datafolder=DATA_FOLDER,
            portable=PORTABLE_MARKER,
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
        self.root.geometry("620x430")
        self.root.resizable(False, False)
        self.installed: Path | None = None

        tk.Label(self.root, text=f"Установка {APP_NAME}", bg=self.BG, fg=self.TEXT,
                 font=("Segoe UI Semibold", 17), anchor="w").place(x=28, y=22)
        tk.Label(self.root,
                 text="Всё будет в одной папке: сам VleyzLauncher.exe, версии игры, Java и логи.",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9), anchor="w").place(x=28, y=56)

        tk.Label(self.root, text="Куда установить (внутри появится папка VleyzLauncher)",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9), anchor="w").place(x=28, y=100)
        self.dir_var = tk.StringVar(value=str(DEFAULT_DIR))
        entry = tk.Entry(self.root, textvariable=self.dir_var, bg=self.FIELD, fg=self.TEXT,
                         insertbackground=self.ACCENT, relief="flat", font=("Segoe UI", 9))
        entry.place(x=28, y=124, width=470, height=30)
        tk.Button(self.root, text="Обзор…", command=self._pick_dir, bg=self.PANEL, fg=self.TEXT,
                  activebackground=self.BORDER, relief="flat", font=("Segoe UI", 9)).place(
            x=508, y=124, width=84, height=30)

        quick = (
            (28, "Рабочий стол", self._home("Desktop")),
            (164, "Документы", self._home("Documents")),
            (300, "По умолчанию", DEFAULT_DIR),
        )
        for x, text, path in quick:
            tk.Button(self.root, text=text, command=lambda p=path: self._set_dir(p),
                      bg=self.PANEL, fg=self.MUTED, activebackground=self.BORDER,
                      relief="flat", font=("Segoe UI", 8)).place(x=x, y=160, width=128, height=24)

        self.desktop_var = tk.BooleanVar(value=True)
        self.menu_var = tk.BooleanVar(value=True)
        tk.Checkbutton(self.root, text="Ярлык на рабочем столе", variable=self.desktop_var,
                       bg=self.BG, fg=self.TEXT, selectcolor=self.PANEL, activebackground=self.BG,
                       activeforeground=self.TEXT, font=("Segoe UI", 9), anchor="w").place(x=28, y=196)
        tk.Checkbutton(self.root, text="Ярлык в меню «Пуск»", variable=self.menu_var,
                       bg=self.BG, fg=self.TEXT, selectcolor=self.PANEL, activebackground=self.BG,
                       activeforeground=self.TEXT, font=("Segoe UI", 9), anchor="w").place(x=28, y=222)

        self.status = tk.Label(self.root, text="Готов к установке", bg=self.BG, fg=self.MUTED,
                               font=("Segoe UI", 9), anchor="w", justify="left", wraplength=560)
        self.status.place(x=28, y=256)

        self.primary = tk.Button(self.root, text="Установить", command=self._install,
                                 bg=self.ACCENT, fg="#0A0D18", activebackground="#5A67E8",
                                 relief="flat", font=("Segoe UI Semibold", 10))
        self.primary.place(x=28, y=320, width=180, height=40)
        self.secondary = tk.Button(self.root, text="Закрыть", command=self.root.destroy,
                                   bg=self.PANEL, fg=self.TEXT, activebackground=self.BORDER,
                                   relief="flat", font=("Segoe UI", 10))
        self.secondary.place(x=220, y=320, width=160, height=40)

    @staticmethod
    def _home(name: str) -> Path:
        return known_folder(name)

    def _set_dir(self, path: Path) -> None:
        self.dir_var.set(str(path))

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
        self.status.configure(
            text=f"Готово. Ваша папка:\n{target.parent}", fg="#7C8CFA"
        )
        self.primary.configure(text="Запустить лаунчер", bg="#4ADE80", fg="#06220F",
                               activebackground="#22B364", command=self._launch)
        self.secondary.configure(text="Открыть папку", command=self._open_folder)

    def _open_folder(self) -> None:
        if self.installed is not None:
            os.startfile(str(self.installed.parent))  # noqa: S606 - открываем проводник

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
