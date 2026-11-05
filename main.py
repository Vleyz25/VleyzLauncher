from __future__ import annotations

import argparse
import logging
import sys
import threading

from vleyzlauncher import APP_VERSION
from vleyzlauncher.core import elyby as elyby_core
from vleyzlauncher.core import java as java_core
from vleyzlauncher.core import instances as instances_core
from vleyzlauncher.core import launch as launch_core
from vleyzlauncher.core import loaders, paths, servers, versions
from vleyzlauncher.core import microsoft as microsoft_core
from vleyzlauncher.core.logging_setup import setup_logging

log = logging.getLogger("vleyz.main")

CLI_FLAGS = ("--version", "--list", "--install", "--command", "-h", "--help")


def attach_console() -> None:
    """Windowed-exe не имеет stdout: для CLI-флагов цепляемся к консоли cmd."""
    if sys.platform != "win32" or sys.stdout is not None:
        return
    if not any(flag in sys.argv[1:] for flag in CLI_FLAGS):
        return
    try:
        import ctypes

        if not ctypes.windll.kernel32.AttachConsole(-1):
            return
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        pass


def cli_install(game: str, loader: str) -> int:
    from vleyzlauncher.core.installer import Installer, Progress

    progress = Progress()

    def log(message: str) -> None:
        print("   ", message, flush=True)

    cancel = threading.Event()
    cfg = paths.load_config()
    installer = Installer(paths.data_dir(cfg), progress, log=log, cancel=cancel)
    try:
        result = installer.install(game, loader)
    except Exception as exc:  # noqa: BLE001 - CLI показывает текст ошибки
        print(f"ОШИБКА: {exc}")
        return 1
    print(f"ГОТОВО: {result['version_id']} · {paths.human_size(result['size'])}")
    return 0


def cli_list() -> int:
    cfg = paths.load_config()
    data_dir = paths.data_dir(cfg)
    installed = loaders.installed_versions(data_dir)
    if not installed:
        print("Установленных версий нет.")
        return 0
    for item in installed:
        print(f"{item['game']:<10} {item['label']:<20} {paths.human_size(item['size']):>10}  {item['id']}")
    print(f"\nВсего: {len(installed)} · папка {data_dir}")
    return 0


def cli_command(game: str, loader: str, nickname: str, ram_mb: int, server_addr: str = "") -> int:
    cfg = paths.load_config()
    data_dir = paths.data_dir(cfg)
    version_id = loaders.find_installed(data_dir, game, loader)
    if not version_id:
        print(f"{game} ({loader}) не установлена")
        return 1
    needed = versions.java_requirement(data_dir, version_id)
    found = java_core.find_java(needed, cfg.get("java_path", ""))
    if not found:
        print(f"Нужна Java {needed}, подходящей нет")
        return 1
    server = None
    if server_addr:
        server = servers.resolve_address(server_addr)
        if not server:
            print(f"Некорректный адрес сервера: {server_addr}")
            return 1
    game_dir = instances_core.ensure(data_dir, version_id)
    servers.sync_servers_dat(game_dir, cfg)
    session = None
    mode = str(cfg.get("account_mode") or "offline")
    try:
        if mode == "microsoft" and microsoft_core.connected(cfg):
            session = microsoft_core.ensure_session(cfg)
            paths.save_config(cfg)
        elif mode == "elyby" and elyby_core.connected(cfg):
            session = elyby_core.launch_session(cfg, data_dir)
            paths.save_config(cfg)
    except (microsoft_core.AuthError, elyby_core.AuthError) as exc:
        print(f"ОШИБКА входа: {exc}")
        return 1
    if session:
        nickname = str(session.get("profile_name") or nickname)
    command = launch_core.build_command(
        data_dir, version_id, nickname, ram_mb, found[0], server, session,
        str(cfg.get("jvm_args") or ""), game_dir,
    )
    print(" ".join(command))
    return 0


def main(argv: list[str] | None = None) -> int:
    attach_console()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(prog="VleyzLauncher")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    parser.add_argument("--install", metavar="VERSION", help="скачать версию без запуска интерфейса")
    parser.add_argument("--loader", default="vanilla", choices=[item["id"] for item in loaders.LOADERS],
                        help="загрузчик для --install (по умолчанию vanilla)")
    parser.add_argument("--list", action="store_true", help="показать установленные версии")
    parser.add_argument("--command", metavar="VERSION", help="напечатать команду запуска")
    parser.add_argument("--nick", default="", help="никнейм для --command")
    parser.add_argument("--ram", type=int, default=0, help="память в МБ для --command")
    parser.add_argument("--server", metavar="HOST[:PORT]", default="",
                        help="сервер для автоподключения в --command")
    args = parser.parse_args(argv)
    gui = not (args.list or args.install or args.command)
    log_path = setup_logging(show_dialog=gui)
    log.info("запуск в режиме %s, лог %s", "gui" if gui else "cli", log_path)

    if args.list:
        return cli_list()

    if args.install:
        return cli_install(args.install, args.loader)

    if args.command:
        cfg = paths.load_config()
        nickname = (args.nick or cfg.get("nickname") or "Steve")[:16]
        ram = args.ram or int(cfg.get("ram_mb", 2048))
        return cli_command(args.command, args.loader, nickname, ram, args.server)

    from vleyzlauncher.ui.app import Launcher

    app = Launcher()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
