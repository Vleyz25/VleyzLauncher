from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

from .. import APP_NAME, APP_VERSION, i18n
from ..core import java as java_core
from ..core import launch as launch_core
from ..core import loaders
from ..core import elyby as elyby_core
from ..core import backups as backups_core
from ..core import instances as instances_core
from ..core import microsoft as microsoft_core
from ..core import modpacks as modpacks_core
from ..core import mods as mods_core
from ..core import modrinth as modrinth_core
from ..core import modupdates as modupdates_core
from ..core import packs as packs_core
from ..core import paths
from ..core import profiles as profiles_core
from ..core import servers as servers_core
from ..core import skins as skins_core
from ..core import updates as updates_core
from ..core import versions as version_core
from ..core.installer import InstallError, Installer, Progress, uninstall
from ..core.net import DownloadCancelled
from . import draw, theme

log = logging.getLogger("vleyz.ui")

W, H = 1180, 724
TITLE_H = 46
SIDE_W = 236

CX0, CX1 = 272, 1144
LEFT_X1 = 866
RIGHT_X0 = 886

SEARCH_Y0, SEARCH_Y1 = 116, 152
GRID_TOP = 164
GRID_COLS, GRID_ROWS = 4, 7
CELL_W, CELL_H, CELL_GAP = 142, 58, 8
PAGE_SIZE = GRID_COLS * GRID_ROWS

RP_X0, RP_X1 = 906, 1124
LOADER_TOP, LOADER_STEP, LOADER_H = 282, 46, 40
BAR_X0, BAR_X1, BAR_Y, BAR_H = RP_X0, RP_X1, 518, 6

NAV = [
    ("home", "Главная"),
    ("play", "Версии"),
    ("servers", "Серверы"),
    ("library", "Библиотека"),
    ("mods", "Моды"),
    ("settings", "Настройки"),
    ("about", "О лаунчере"),
]


class CanvasButton:
    _seq = 0

    def __init__(self, app, x0, y0, x1, y1, label, command=None, kind="primary", size=10, enabled=True):
        CanvasButton._seq += 1
        self.tag = f"btn{CanvasButton._seq}"
        self.app = app
        self.box = (x0, y0, x1, y1)
        self.kind = kind
        self.command = command
        self.enabled = enabled
        self.hover = False
        self.cv = app.canvas
        self.bg = self._bg()
        self.radius = min(12, (y1 - y0) / 2)
        self.bg_id = draw.rr(self.cv, x0, y0, x1, y1, self.radius, fill=self.bg,
                             outline=self._outline(), width=1, tags=self.tag)
        self.label_id = self.cv.create_text(
            (x0 + x1) / 2, (y0 + y1) / 2, text=i18n.tr(label), tags=self.tag, anchor="center",
            font=(theme.FONT_SEMI, size), fill=self._fg(),
        )
        self.cv.tag_bind(self.tag, "<Enter>", self._enter)
        self.cv.tag_bind(self.tag, "<Leave>", self._leave)
        self.cv.tag_bind(self.tag, "<Button-1>", self._click)

    def _bg(self):
        if self.kind == "primary":
            return theme.ACCENT if self.enabled else theme.PRIMARY_OFF
        if self.kind == "danger":
            return theme.DANGER_BG
        return theme.CARD

    def _fg(self):
        if self.kind == "primary":
            return theme.ON_ACCENT if self.enabled else theme.DIM
        if self.kind == "danger":
            return theme.DANGER if self.enabled else theme.DIM
        return theme.TEXT if self.enabled else theme.DIM

    def _outline(self):
        if self.kind == "danger":
            return theme.DANGER_BORDER
        return theme.BORDER if self.kind != "primary" else ""

    def _enter(self, _event=None):
        if not self.enabled:
            return
        self.hover = True
        fill = {
            "primary": draw.mix(theme.ACCENT, "#FFFFFF", 0.14),
            "danger": theme.DANGER_HOVER,
        }.get(self.kind, theme.BUTTON_HOVER)
        self.cv.itemconfigure(self.bg_id, fill=fill)
        self.cv.configure(cursor="hand2")

    def _leave(self, _event=None):
        self.hover = False
        self.cv.itemconfigure(self.bg_id, fill=self._bg())
        self.cv.configure(cursor="")

    def _click(self, _event=None):
        if self.enabled and self.command:
            self.command()

    def set_enabled(self, enabled: bool):
        if self.enabled == enabled:
            return
        self.enabled = enabled
        self.cv.itemconfigure(self.bg_id, fill=self._bg(), outline=self._outline())
        self.cv.itemconfigure(self.label_id, fill=self._fg())

    def set_label(self, label: str):
        self.cv.itemconfigure(self.label_id, text=i18n.tr(label))

    def set_kind(self, kind: str):
        self.kind = kind
        self.cv.itemconfigure(self.bg_id, fill=self._bg(), outline=self._outline())
        self.cv.itemconfigure(self.label_id, fill=self._fg())

    def destroy(self):
        self.cv.delete(self.tag)


class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.cfg = paths.load_config()
        theme.apply(str(self.cfg.get("theme") or "dark"))
        i18n.set_language(str(self.cfg.get("language") or "ru"))
        profiles_core.ensure(self.cfg)
        self.data_owned = False
        try:
            self.data_owned = paths.ensure_data_root(paths.data_dir(self.cfg))
            if getattr(sys, "frozen", False):
                paths.mark_install(paths.app_dir())
        except OSError:
            self.data_owned = False
        self.game = str(self.cfg.get("selected_version") or "")
        self.loader = str(self.cfg.get("selected_loader") or "vanilla")
        if self.loader not in loaders.LOADER_BY_ID:
            self.loader = "vanilla"
        self.page = "home"
        self.modal: str | None = None
        self.modal_buttons: list[CanvasButton] = []
        self.progress = Progress()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.log_lines: list[str] = []
        self.releases: list[dict] | None = None
        self.releases_loading = False
        self.releases_error = ""
        self.query = ""
        self.grid_page = 0
        self.choices: dict[str, dict] = {}
        self.choices_game = ""
        self.installed_map: dict[str, set[str]] = {}
        self.installed_id: str | None = None
        self.installed_size = 0
        self.plan_error = ""
        self.java_required = 8
        self.status_text = "Готов к работе"
        self.java_text = "Java: проверяю…"
        self.java_ok = False
        self.java_busy = False
        self._display_pct = 0.0
        self._confirm_delete: str | None = None
        self.library_offset = 0
        self.mods_offset = 0
        self.servers_pings: dict[str, dict] = {}
        self._ping_busy: set[str] = set()
        self._server_edit_id: str | None = None
        self._mods_signature: tuple | None = None
        self._size_cache: dict[str, int] = {}
        self.launch_worker: threading.Thread | None = None
        self.launching_id: str | None = None
        self.running_id: str | None = None
        self._ely_stage = ""  # "" | "2fa" — форма входа Ely.by
        self._ely_busy = False
        self._account_tab = "offline"
        self._acct_error = ""
        self._ms = {"stage": "", "busy": False, "code": "", "uri": "", "opened": False}
        self._ms_cancel = threading.Event()
        self._queue: queue.Queue = queue.Queue()
        self._restore_search_focus = False
        self.update_info: dict | None = None
        self.update_checked = False
        self._update_busy = False
        self._update_prompted = False
        self.update_button: CanvasButton | None = None
        self._error_shown = False
        self._install_repair = False
        self._mr = {
            "query": "",
            "results": [],
            "page": 0,
            "busy": False,
            "error": "",
            "status": "",
            "installing": "",
        }
        self._packs = {
            "kind": "resourcepack",
            "query": "",
            "results": [],
            "page": 0,
            "busy": False,
            "error": "",
            "status": "",
            "installing": "",
        }
        self._mu = {"busy": False, "checked": False, "updates": [], "error": "",
                    "status": "", "applying": "", "stats": {}}
        self._bk = {"busy": False, "items": [], "page": 0, "error": "",
                    "status": "", "restoring": ""}
        self._skin = {"nick": "", "busy": False, "error": "", "path": None, "image": None}

        self.withdraw()
        self.overrideredirect(True)
        self.configure(bg=theme.BG_TOP)
        self.geometry(f"{W}x{H}+{(self.winfo_screenwidth() - W) // 2}+{max(0, (self.winfo_screenheight() - H) // 2 - 20)}")
        self.canvas = tk.Canvas(self, width=W, height=H, highlightthickness=0, bg=theme.BG_TOP)
        self.canvas.pack(fill="both", expand=True)

        self._entry_font = (theme.FONT, 10)
        self.entries: dict[str, tk.Entry] = {}

        self._scan_installed()
        self._draw_background()
        self._draw_titlebar()
        self._draw_sidebar()
        self._show_page("home")
        self._draw_taskbar_icon()
        self.deiconify()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Map>", self._on_map)
        self.bind("<FocusIn>", self._on_focus)
        self.bind("<Escape>", lambda e: self._close_modal())
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self._load_releases()
        self.after(1200, self._check_updates)
        if self.game:
            self._resolve_choices()
            self._detect_java()
        self.after(120, self._poll)

    # ------------------------------------------------------------------ фон

    def _draw_background(self):
        cv = self.canvas
        draw.gradient_vertical(cv, 0, 0, W, H, theme.BG_TOP, theme.BG_BOTTOM)
        # мягкое световое пятно сверху для глубины
        draw.gradient_vertical(cv, 0, 0, W, 240, draw.mix(theme.BG_TOP, theme.ACCENT, 0.07),
                               theme.BG_TOP, tags=("bg_glow",))

    def _draw_titlebar(self):
        cv = self.canvas
        cv.create_rectangle(0, 0, W, TITLE_H, fill=theme.TITLEBAR, outline="")
        cv.create_line(0, TITLE_H, W, TITLE_H, fill=theme.DIVIDER)
        draw.cube(cv, 24, 23, 16, theme.ACCENT, tags="drag")
        draw.text(cv, 40, 23, "VLEYZ", 10, theme.TEXT, "bold", "w", "drag")
        draw.text(cv, 82, 23, "LAUNCHER", 9, theme.DIM, "normal", "w", "drag")
        draw.text(cv, 156, 23, "официальные файлы Mojang", 8, theme.DIM, "normal", "w", "drag")

        self.close_id = draw.rr(cv, W - 60, 10, W - 28, 36, 8, fill=theme.TITLEBAR, outline="", tags="win_close")
        self.min_id = draw.rr(cv, W - 96, 10, W - 64, 36, 8, fill=theme.TITLEBAR, outline="", tags="win_min")
        cv.create_line(W - 88, 24, W - 72, 24, fill=theme.MUTED, width=1.4, tags="win_min")
        cv.create_line(W - 52, 18, W - 36, 34, fill=theme.MUTED, width=1.4, tags="win_close")
        cv.create_line(W - 36, 18, W - 52, 34, fill=theme.MUTED, width=1.4, tags="win_close")

        cv.tag_bind("win_close", "<Enter>", lambda e: cv.itemconfigure(self.close_id, fill=theme.CLOSE_HOVER))
        cv.tag_bind("win_close", "<Leave>", lambda e: cv.itemconfigure(self.close_id, fill=theme.TITLEBAR))
        cv.tag_bind("win_close", "<Button-1>", lambda e: self._on_close())
        cv.tag_bind("win_min", "<Enter>", lambda e: cv.itemconfigure(self.min_id, fill=theme.HOVER))
        cv.tag_bind("win_min", "<Leave>", lambda e: cv.itemconfigure(self.min_id, fill=theme.TITLEBAR))
        cv.tag_bind("win_min", "<Button-1>", lambda e: self._minimize())

        cv.tag_bind("drag", "<Button-1>", self._drag_start)
        cv.tag_bind("drag", "<B1-Motion>", self._drag_move)

    def _drag_start(self, event):
        self._drag = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _drag_move(self, event):
        if not hasattr(self, "_drag"):
            return
        dx, dy = self._drag
        self.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _minimize(self):
        try:
            self.overrideredirect(False)
            self.iconify()
        except tk.TclError:
            pass

    def _on_map(self, _event=None):
        if self.state() == "normal" and not self.overrideredirect():
            self.overrideredirect(True)

    def _draw_taskbar_icon(self):
        candidates = [paths.app_dir() / "assets" / "vleyz.ico"]
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            candidates.append(Path(bundle) / "assets" / "vleyz.ico")
        for icon in candidates:
            if not icon.is_file():
                continue
            try:
                self.iconbitmap(str(icon))
                return
            except tk.TclError:
                continue

    # -------------------------------------------------------------- сайдбар

    def _draw_sidebar(self):
        cv = self.canvas
        cv.create_rectangle(0, TITLE_H, SIDE_W, H, fill=theme.SIDEBAR, outline="")
        cv.create_line(SIDE_W, TITLE_H, SIDE_W, H, fill=theme.DIVIDER)

        draw.cube(cv, 54, 92, 44, theme.ACCENT)
        draw.text(cv, 88, 82, "VLEYZ", 17, theme.TEXT, "bold")
        draw.text(cv, 88, 104, "LAUNCHER", 8, theme.MUTED)
        draw.hline(cv, 18, SIDE_W - 18, 136, theme.DIVIDER)

        self.nav_ids: dict[str, dict] = {}
        for index, (key, label) in enumerate(NAV):
            y0 = 158 + index * 48
            y1 = y0 + 42
            self._draw_nav_item(key, label, y0, y1)

        box_top, box_bottom = H - 108, H - 26
        draw.rr(cv, 16, box_top, SIDE_W - 16, box_bottom, 14, fill=theme.FIELD, outline=theme.PANEL_BORDER)
        draw.cube(cv, 40, box_top + 26, 18, theme.ACCENT)
        draw.text(cv, 60, box_top + 19, "Сделал vleyz", 10, theme.TEXT, "bold")
        draw.text(cv, 60, box_top + 36, f"v{APP_VERSION} · сетевая игра", 8, theme.MUTED)
        draw.text(cv, 30, box_bottom - 16, "Игра по сети · официальные файлы Mojang", 8, theme.DIM)
        self._draw_update_badge()

    def _draw_update_badge(self) -> None:
        """Кнопка обновления в свободном месте сайдбара."""
        if self.update_button is not None:
            self.update_button.destroy()
            self.update_button = None
        info = self.update_info
        if not info:
            return
        self.update_button = CanvasButton(
            self, 16, 524, SIDE_W - 16, 574,
            f"Обновление {info.get('version') or ''}".strip(),
            command=self._open_update, kind="primary", size=9,
        )

    def _check_updates(self, manual: bool = False) -> None:
        if self._update_busy:
            return
        self._update_busy = True

        def work() -> None:
            info = updates_core.check()
            self._post(lambda: self._update_ready(info, manual))

        threading.Thread(target=work, daemon=True, name="update-check").start()

    def _update_ready(self, info: dict | None, manual: bool) -> None:
        self._update_busy = False
        self.update_checked = True
        if info:
            self.update_info = info
            self._draw_update_badge()
            if manual:
                messagebox.showinfo(
                    i18n.tr("Обновление"),
                    i18n.tr(
                        f"Доступна версия {info['version']}.\nСейчас у вас {APP_VERSION}.\n\n"
                        "Нажмите кнопку «Обновление» в левом меню, чтобы скачать установщик."
                    ),
                )
            elif not self._update_prompted:
                self._update_prompted = True
                self.after(300, self._prompt_update)
            return
        if manual:
            messagebox.showinfo(i18n.tr("Обновление"),
                                i18n.tr(f"У вас последняя версия — {APP_VERSION}."))

    def _prompt_update(self) -> None:
        """Само предложение обновиться при запуске: окно, а не только кнопка."""
        info = self.update_info
        if not info:
            return
        version = info.get("version") or i18n.tr("новую версию")
        if not messagebox.askyesno(
            i18n.tr("Обновление"),
            i18n.tr(
                f"Вышла версия {version}.\nСейчас у вас {APP_VERSION}.\n\n"
                "Скачать и установить сейчас? Лаунчер закроется и запустится снова."
            ),
        ):
            return
        if not getattr(sys, "frozen", False) or not info.get("setup_url"):
            webbrowser.open(updates_core.download_url(info))
            return
        self._download_update(info)

    def _open_update(self) -> None:
        info = self.update_info or {}
        url = updates_core.download_url(info)
        if not url:
            return
        if not getattr(sys, "frozen", False) or not info.get("setup_url"):
            # Из исходников нельзя заменить запущенный процесс — ведём на релиз.
            webbrowser.open(url)
            return
        version = info.get("version") or i18n.tr("новую версию")
        if not messagebox.askyesno(
            i18n.tr("Обновление"),
            i18n.tr(
                f"Скачать и установить {version}?\n\n"
                "Лаунчер закроется и запустится снова. Настройки, профили и миры останутся."
            ),
        ):
            return
        self._download_update(info)

    def _download_update(self, info: dict) -> None:
        if self._update_busy:
            return
        self._update_busy = True
        if self.update_button is not None:
            self.update_button.set_enabled(False)
            self.update_button.set_label("Скачиваю…")
        total = int(info.get("setup_size") or 0)
        dest = Path(tempfile.gettempdir()) / f"VleyzLauncher-Setup-{info.get('version') or 'new'}.exe"
        state = {"done": 0, "pct": -1}

        def on_bytes(count: int) -> None:
            state["done"] = max(0, state["done"] + count)
            if total <= 0:
                return
            percent = max(0, min(100, int(state["done"] * 100 / total)))
            if percent != state["pct"]:
                state["pct"] = percent
                self._post(lambda value=percent: self._update_label(f"Скачиваю {value} %"))

        def work() -> None:
            try:
                updates_core.download_update(info, dest, on_bytes=on_bytes)
                error = ""
            except Exception as exc:  # noqa: BLE001 - показываем пользователю
                error = str(exc)
            self._post(lambda: self._update_downloaded(dest, error))

        threading.Thread(target=work, daemon=True, name="update-download").start()

    def _update_label(self, text: str) -> None:
        if self.update_button is not None:
            try:
                self.update_button.set_label(text)
            except tk.TclError:
                pass

    def _update_downloaded(self, dest: Path, error: str) -> None:
        self._update_busy = False
        if error:
            if self.update_button is not None:
                self.update_button.set_enabled(True)
                self.update_button.set_label(
                    f"Обновление {((self.update_info or {}).get('version') or '').strip()}".strip()
                )
            messagebox.showerror(i18n.tr("Обновление"),
                                 i18n.tr(f"Не удалось скачать обновление:\n{error}"))
            return
        target = paths.app_dir()
        try:
            if getattr(sys, "frozen", False) and os.access(target, os.W_OK):
                subprocess.Popen(
                    updates_core.install_command(dest, target),
                    creationflags=0x08000000,
                    close_fds=True,
                )
            else:
                os.startfile(str(dest))
        except OSError as exc:
            messagebox.showerror(i18n.tr("Обновление"),
                                 i18n.tr(f"Не удалось запустить установщик:\n{exc}"))
            return
        self.after(500, self._on_close)

    def _draw_nav_item(self, key, label, y0, y1):
        cv = self.canvas
        tag = f"nav_{key}"
        active = key == self.page
        bg = draw.rr(cv, 14, y0, SIDE_W - 14, y1, 10,
                     fill=theme.SELECT_BG if active else theme.SIDEBAR, outline="", tags=(tag,))
        bar = draw.rr(cv, 14, y0 + 9, 17, y1 - 9, 2,
                      fill=theme.ACCENT if active else theme.SIDEBAR, outline="", tags=(tag,))
        icon_y = (y0 + y1) / 2
        color = theme.ACCENT if active else theme.MUTED
        self._nav_icon(key, 40, icon_y, color, tag)
        label_id = draw.text(cv, 60, icon_y, label, 10, theme.TEXT if active else theme.MUTED,
                             "bold" if active else "normal", "w", (tag,))
        self.nav_ids[key] = {"bg": bg, "bar": bar, "label": label_id, "y0": y0, "y1": y1}
        cv.tag_bind(tag, "<Button-1>", lambda e, k=key: self._show_page(k))
        cv.tag_bind(tag, "<Enter>", lambda e, k=key: self._nav_hover(k, True))
        cv.tag_bind(tag, "<Leave>", lambda e, k=key: self._nav_hover(k, False))

    def _nav_icon(self, key, x, y, color, tag):
        cv = self.canvas
        if key == "play":
            cv.create_polygon(x, y - 6, x, y + 6, x + 9, y, fill=color, outline="", tags=(tag,))
        elif key == "library":
            for dx in (0, 7):
                for dy in (0, 7):
                    cv.create_rectangle(x - 5 + dx, y - 5 + dy, x - 1 + dx, y - 1 + dy,
                                        fill=color, outline="", tags=(tag,))
        elif key == "settings":
            cv.create_oval(x - 3, y - 3, x + 3, y + 3, outline=color, width=1.6, tags=(tag,))
            cv.create_oval(x - 7, y - 7, x + 7, y + 7, outline=color, width=1.6, tags=(tag,))
        elif key == "mods":
            cv.create_rectangle(x - 7, y - 6, x + 1, y + 6, outline=color, width=1.4, tags=(tag,))
            cv.create_rectangle(x - 1, y - 1, x + 7, y + 6, fill=color, outline="", tags=(tag,))
        elif key == "servers":
            for dy in (-5, 4):
                cv.create_rectangle(x - 8, y + dy - 3, x + 8, y + dy + 3,
                                    outline=color, width=1.4, tags=(tag,))
                cv.create_oval(x + 3, y + dy - 1.5, x + 6, y + dy + 1.5,
                               fill=color, outline="", tags=(tag,))
        else:
            cv.create_oval(x - 7, y - 7, x + 7, y + 7, outline=color, width=1.4, tags=(tag,))
            cv.create_text(x, y, text="i", fill=color, font=(theme.FONT_SEMI, 8), tags=(tag,))

    def _nav_hover(self, key, entering):
        if key == self.page:
            return
        cv = self.canvas
        cv.itemconfigure(self.nav_ids[key]["bg"], fill=theme.HOVER if entering else theme.SIDEBAR)
        cv.itemconfigure(self.nav_ids[key]["label"], fill=theme.TEXT_SOFT if entering else theme.MUTED)
        cv.configure(cursor="hand2" if entering else "")

    def _refresh_nav(self):
        cv = self.canvas
        for key, ids in self.nav_ids.items():
            active = key == self.page
            cv.itemconfigure(ids["bg"], fill=theme.SELECT_BG if active else theme.SIDEBAR)
            cv.itemconfigure(ids["bar"], fill=theme.ACCENT if active else theme.SIDEBAR)
            cv.itemconfigure(
                ids["label"],
                fill=theme.TEXT if active else theme.MUTED,
                font=(theme.FONT_SEMI if active else theme.FONT, 10, "bold" if active else "normal"),
            )

    # ------------------------------------------------------------------ entry

    def _entry(self, name: str, value: str, secret: bool = False):
        entry = tk.Entry(
            self, bd=0, relief="flat", bg=theme.FIELD, fg=theme.TEXT, insertbackground=theme.ACCENT,
            font=self._entry_font, highlightthickness=0, exportselection=False, insertwidth=2,
            show="•" if secret else "",
        )
        entry.insert(0, value)
        self.entries[name] = entry
        return entry

    def _place_entry(self, name, x, y, width, height):
        entry = self.entries[name]
        entry.place(x=x + 12, y=y + (height - 18) // 2, width=width - 24, height=18)
        entry.lift()

    def _hide_entries(self):
        for entry in self.entries.values():
            entry.place_forget()

    def _post(self, callback):
        """Единственный потокобезопасный способ тронуть UI из рабочего потока."""
        self._queue.put(callback)

    def _drain_queue(self):
        while True:
            try:
                callback = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                callback()
            except tk.TclError:
                continue
            except Exception:  # noqa: BLE001 - один сломанный колбэк не должен ронять окно
                continue

    # ---------------------------------------------------------------- страницы

    def _show_page(self, key):
        if self.modal:
            self._close_modal(rebuild=False)
        self.page = key
        self.canvas.delete("page")
        self._hide_entries()
        for button in getattr(self, "page_buttons", []):
            button.destroy()
        self.page_buttons: list[CanvasButton] = []
        self._refresh_nav()
        self._scan_installed()
        if key == "home":
            self._draw_home()
        elif key == "play":
            self._draw_play()
        elif key == "servers":
            self._draw_servers()
        elif key == "library":
            self._draw_library()
        elif key == "mods":
            self._draw_mods()
        elif key == "settings":
            self._draw_settings()
        else:
            self._draw_about()

    def _header(self, title, subtitle):
        draw.text(self.canvas, CX0, 74, title, 20, theme.TEXT, "bold", "w", "page")
        draw.text(self.canvas, CX0, 102, subtitle, 9, theme.MUTED, "normal", "w", "page")

    # ------------------------------------------------------------- состояние

    def _data_dir(self) -> Path:
        return paths.data_dir(self.cfg)

    def _instance_meta(self) -> tuple[str, str]:
        """Версия и загрузчик выбранной установленной сборки."""
        if self.installed_id:
            for row in loaders.installed_versions(self._data_dir()):
                if row["id"] == self.installed_id:
                    return str(row["game"]), str(row["loader"])
        return self.game, self.loader

    def _accent(self) -> str:
        return theme.loader_accent(self.loader)

    def _scan_installed(self):
        rows = loaders.installed_versions(self._data_dir())
        self.installed_map = {}
        self.installed_id = None
        self.installed_size = 0
        for row in rows:
            self.installed_map.setdefault(row["game"], set()).add(row["loader"])
            if row["game"] == self.game and row["loader"] == self.loader:
                self.installed_id = row["id"]
                self.installed_size = row["size"]

    def _choice(self, loader_id: str) -> dict:
        return self.choices.get(loader_id) or {}

    def _loader_label(self) -> str:
        choice = self._choice(self.loader)
        name = loaders.LOADER_BY_ID.get(self.loader, {}).get("label", "Vanilla")
        if choice.get("state") == "ok" and choice.get("version"):
            return f"{name} {choice['version']}"
        return loaders.preview(self.game, self.loader)

    def _loader_version_text(self, loader_id: str) -> str:
        choice = self._choice(loader_id)
        state = choice.get("state")
        if loader_id == "vanilla":
            return "чистая игра"
        if state == "ok":
            return str(choice.get("version") or "")
        if state == "loading":
            return "…"
        if state == "error":
            return "нет"
        return ""

    def _resolve_choices(self):
        game = self.game
        if not game:
            return
        self.choices = {item["id"]: {"state": "loading"} for item in loaders.LOADERS}
        self.choices["vanilla"] = {"state": "ok", "version": ""}
        self.choices_game = game
        self.plan_error = ""

        def run():
            result: dict[str, dict] = {}
            for item in loaders.LOADERS:
                loader_id = item["id"]
                if loader_id == "vanilla":
                    result[loader_id] = {"state": "ok", "version": ""}
                    continue
                try:
                    plan = loaders.plan(self._data_dir(), game, loader_id)
                    result[loader_id] = {"state": "ok", "version": plan.get("loader_version", "")}
                except Exception as exc:  # noqa: BLE001
                    result[loader_id] = {"state": "error", "message": str(exc)}
            if self.game != game:
                return
            self.choices = result
            if self._choice(self.loader).get("state") == "error":
                self.plan_error = str(self._choice(self.loader).get("message") or "")
            self._post(self._refresh_play)

        threading.Thread(target=run, daemon=True).start()

    def _refresh_play(self):
        if self.page in ("play", "home"):
            self._show_page(self.page)

    def _status_line(self) -> tuple[str, str]:
        if not self.game:
            return "Версия не выбрана", theme.MUTED
        if self.installed_id:
            return f"Установлена · {paths.human_size(self.installed_size)}", theme.ACCENT
        choice = self._choice(self.loader)
        if self.plan_error:
            return self.plan_error, theme.DANGER
        if choice.get("state") == "loading":
            return "Проверяю загрузчик…", theme.MUTED
        if choice.get("state") == "error":
            return "Загрузчик недоступен для этой версии", theme.DANGER
        return "Не установлена", theme.MUTED

    # ------------------------------------------------------------- страница игра

    def _draw_play(self):
        self._header("Версии Minecraft", "Все релизы Mojang · выберите версию и загрузчик справа")
        self._draw_search_row()
        self._draw_grid()
        self._draw_right_panel()

    def _draw_search_row(self):
        cv = self.canvas
        tag = "page"
        draw.rr(cv, CX0, SEARCH_Y0, CX0 + 300, SEARCH_Y1, 10, fill=theme.FIELD,
                outline=theme.BORDER, width=1, tags=tag)
        entry = self.entries.get("search") or self._entry("search", self.query)
        entry.place(x=CX0 + 12, y=SEARCH_Y0 + (SEARCH_Y1 - SEARCH_Y0 - 18) // 2, width=180, height=18)
        entry.lift()
        entry.bind("<KeyRelease>", self._on_search)
        if self._restore_search_focus:
            self._restore_search_focus = False
            try:
                entry.focus_set()
                entry.icursor("end")
            except tk.TclError:
                pass
        draw.text(cv, CX0 + 202, (SEARCH_Y0 + SEARCH_Y1) / 2, "id версии", 8, theme.DIM, "normal", "w", tag)

        found = len(self._filtered())
        total = len(self.releases or [])
        if self.releases_loading:
            info = "загружаю список версий…"
        elif self.releases_error:
            info = self.releases_error
        elif self.query:
            info = f"найдено {found} из {total}"
        else:
            info = f"{total} релизов"
        draw.text(cv, CX0 + 316, (SEARCH_Y0 + SEARCH_Y1) / 2, info, 8, theme.MUTED, "normal", "w", tag)

        pages = max(1, (found + PAGE_SIZE - 1) // PAGE_SIZE)
        prev = CanvasButton(self, 736, 119, 770, 149, "←", command=lambda: self._grid_go(-1),
                            kind="ghost", size=9, enabled=self.grid_page > 0)
        nxt = CanvasButton(self, 830, 119, 864, 149, "→", command=lambda: self._grid_go(1),
                           kind="ghost", size=9, enabled=self.grid_page < pages - 1)
        self.page_buttons.extend([prev, nxt])
        draw.text(cv, 783, (SEARCH_Y0 + SEARCH_Y1) / 2, f"{self.grid_page + 1} / {pages}", 8,
                  theme.MUTED, "normal", "center", tag)

    def _draw_grid(self):
        cv = self.canvas
        items = self._filtered()
        pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.grid_page = min(self.grid_page, pages - 1)
        chunk = items[self.grid_page * PAGE_SIZE:(self.grid_page + 1) * PAGE_SIZE]

        if self.releases_loading:
            draw.text(cv, CX0, GRID_TOP + 40, "Загружаю официальный список версий…", 10,
                      theme.MUTED, "normal", "w", "page")
            return
        if not chunk:
            draw.text(cv, CX0, GRID_TOP + 40, "Ничего не найдено", 10, theme.MUTED, "normal", "w", "page")
            return

        for index, version in enumerate(chunk):
            col, row = index % GRID_COLS, index // GRID_COLS
            x0 = CX0 + col * (CELL_W + CELL_GAP)
            y0 = GRID_TOP + row * (CELL_H + CELL_GAP)
            vid = str(version.get("id") or "")
            selected = vid == self.game
            tag = f"ver_{vid.replace('.', '_')}"
            border = draw.rr(cv, x0, y0, x0 + CELL_W, y0 + CELL_H, 10,
                             fill=theme.SELECT_BG if selected else theme.CARD,
                             outline=theme.ACCENT if selected else theme.BORDER, width=2 if selected else 1,
                             tags=("page", tag))
            draw.text(cv, x0 + 12, y0 + 22, vid, 10,
                      theme.ACCENT if selected else theme.TEXT, "bold", "w", ("page", tag))
            draw.text(cv, x0 + 12, y0 + 40, (version.get("releaseTime") or "")[:10], 7,
                      theme.DIM, "normal", "w", ("page", tag))
            if self.installed_map.get(vid):
                draw.dot(cv, x0 + CELL_W - 13, y0 + 14, 3, theme.ACCENT, tags=("page", tag))
                draw.text(cv, x0 + CELL_W - 21, y0 + 41, f"{len(self.installed_map[vid])}", 7,
                          theme.DIM, "normal", "e", ("page", tag))
            cv.tag_bind(tag, "<Button-1>", lambda e, v=vid: self._select_game(v))
            cv.tag_bind(tag, "<Enter>", lambda e, b=border, s=selected: None if s else
                        (cv.itemconfigure(b, outline=theme.BORDER_HOVER), cv.configure(cursor="hand2")))
            cv.tag_bind(tag, "<Leave>", lambda e, b=border, s=selected: None if s else
                        (cv.itemconfigure(b, outline=theme.BORDER), cv.configure(cursor="")))

    def _filtered(self) -> list[dict]:
        items = self.releases or []
        query = self.query.strip().lower()
        if not query:
            return items
        return [version for version in items if query in str(version.get("id", "")).lower()]

    def _on_search(self, _event=None):
        entry = self.entries.get("search")
        self.query = entry.get() if entry is not None else ""
        self.grid_page = 0
        self._restore_search_focus = True
        self._show_page("play")

    def _grid_go(self, delta):
        self.grid_page = max(0, self.grid_page + delta)
        self._show_page("play")

    def _select_game(self, vid: str):
        if vid == self.game:
            return
        self.game = vid
        self.cfg["selected_version"] = vid
        paths.save_config(self.cfg)
        self.plan_error = ""
        self._display_pct = 0.0
        self.choices = {item["id"]: {"state": "loading"} for item in loaders.LOADERS}
        self.choices["vanilla"] = {"state": "ok", "version": ""}
        self._scan_installed()
        self._show_page("play")
        self._resolve_choices()
        self._detect_java()

    def _select_loader(self, loader_id: str):
        if loader_id == self.loader:
            return
        self.loader = loader_id
        self.cfg["selected_loader"] = loader_id
        paths.save_config(self.cfg)
        self.plan_error = ""
        if self._choice(loader_id).get("state") == "error":
            self.plan_error = str(self._choice(loader_id).get("message") or "")
        self._display_pct = 0.0
        self._scan_installed()
        self._show_page("play")

    # ------------------------------------------------------------ правая панель

    def _draw_right_panel(self):
        cv = self.canvas
        draw.rr(cv, RIGHT_X0, 136, CX1, 688, 18, fill=theme.PANEL,
                outline=theme.PANEL_BORDER, tags="page")

        draw.text(cv, RP_X0, 158, "ВЫБРАНО", 8, theme.DIM, "normal", "w", "page")
        draw.text(cv, RP_X0, 182, f"Minecraft {self.game}" if self.game else "—", 18,
                  theme.TEXT, "bold", "w", "page")

        release = version_core.version_date(self._data_dir(), self.game) if self.game else ""
        draw.text(cv, RP_X0, 206, f"релиз {release}" if release else "официальный клиент Mojang",
                  8, theme.MUTED, "normal", "w", "page")

        status_text, status_color = self._status_line()
        draw.dot(cv, RP_X0 + 4, 228, 3.2, status_color, tags="page")
        draw.text(cv, RP_X0 + 14, 228, status_text, 8, status_color, "normal", "w", "page")

        draw.hline(cv, RP_X0, RP_X1, 250, theme.DIVIDER, tags="page")
        draw.text(cv, RP_X0, 268, "ЗАГРУЗЧИК", 8, theme.DIM, "normal", "w", "page")
        for index, item in enumerate(loaders.LOADERS):
            self._draw_loader_row(item, LOADER_TOP + index * LOADER_STEP)

        draw.hline(cv, RP_X0, RP_X1, 474, theme.DIVIDER, tags="page")
        self.stage_id = draw.text(cv, RP_X0, 488, self.status_text, 10, theme.TEXT, "bold", "w", "page")
        self.detail_id = draw.text(cv, RP_X0, 504, self._idle_detail()[:44], 8, theme.MUTED, "normal", "w", "page")

        draw.rr(cv, BAR_X0, BAR_Y, BAR_X1, BAR_Y + BAR_H, BAR_H / 2, fill=theme.TRACK, outline="",
                tags="page")
        self.bar_accent = self._accent()
        self.meta_id = draw.text(cv, BAR_X0, 532, "0 / 0 файлов", 7, theme.DIM, "normal", "w", "page")
        self.pct_id = draw.text(cv, BAR_X1, 532, "0 %", 7, theme.MUTED, "normal", "e", "page")

        busy = bool(self.worker and self.worker.is_alive())
        if busy:
            label, kind, enabled = "Отмена", "danger", True
        elif self.installed_id:
            label, kind, enabled = "Переустановить", "ghost", True
        else:
            label, kind, enabled = "Скачать", "primary", bool(self.game)
        self.btn_primary = CanvasButton(self, RP_X0, 548, RP_X1, 586, label,
                                        command=self._primary_action, kind=kind, size=10, enabled=enabled)
        self.btn_play = CanvasButton(self, RP_X0, 592, RP_X1, 630, "Играть",
                                     command=self._start_game, kind="primary" if self.installed_id else "ghost",
                                     size=10, enabled=bool(self.installed_id))
        self.page_buttons.extend([self.btn_primary, self.btn_play])

        nick = (self.cfg.get("nickname") or "Steve")[:16]
        draw.text(cv, RP_X0, 650, f"ник: {nick}", 7, theme.DIM, "normal", "w", "page")
        draw.text(cv, RP_X1, 650, self.java_text, 7,
                  theme.ACCENT if self.java_ok else theme.DANGER, "normal", "e", "page")
        self.log_id = draw.text(cv, RP_X0, 668, self.log_lines[-1] if self.log_lines else "Готово к установке",
                                7, theme.DIM, "normal", "w", "page", width=218)

    def _draw_loader_row(self, item, y):
        cv = self.canvas
        loader_id = item["id"]
        accent = theme.loader_accent(loader_id)
        selected = loader_id == self.loader
        choice = self._choice(loader_id)
        state = choice.get("state", "")
        enabled = state != "error"
        tag = f"ldr_{loader_id}"

        draw.rr(cv, RP_X0 - 4, y, RP_X1 + 4, y + LOADER_H, 10,
                fill=theme.SELECT_BG if selected else theme.CARD,
                outline=accent if selected else theme.BORDER, width=1,
                tags=("page", tag))
        draw.dot(cv, RP_X0 + 8, y + LOADER_H / 2, 3,
                 accent if (selected or enabled) else theme.DIM, tags=("page", tag))
        draw.text(cv, RP_X0 + 20, y + 20, item["label"], 9,
                  accent if selected else (theme.TEXT if enabled else theme.MUTED),
                  "bold", "w", ("page", tag))
        draw.text(cv, RP_X1 - 6, y + 20, self._loader_version_text(loader_id), 8,
                  theme.MUTED if enabled else theme.DIM, "normal", "e", ("page", tag))
        if loader_id in self.installed_map.get(self.game, set()):
            draw.text(cv, RP_X0 + 20, y + 32, "установлен", 7, theme.ACCENT, "normal", "w",
                      ("page", tag))
        elif state == "error" and choice.get("message"):
            note = str(choice["message"])[:40]
            draw.text(cv, RP_X0 + 20, y + 32, note, 7, theme.DIM, "normal", "w", ("page", tag))

        cv.tag_bind(tag, "<Button-1>", lambda e, l=loader_id: self._select_loader(l))
        cv.tag_bind(tag, "<Enter>", lambda e: cv.configure(cursor="hand2"))
        cv.tag_bind(tag, "<Leave>", lambda e: cv.configure(cursor=""))

    def _idle_detail(self) -> str:
        if not self.game:
            return "Выберите версию в списке слева"
        if self.plan_error:
            return self.plan_error
        if self.installed_id:
            return f"папка versions\\{self.installed_id}"
        return "файлы с официальных серверов"

    # ------------------------------------------------------------------ главная

    def _installed_rows(self) -> list[dict]:
        return loaders.installed_versions(self._data_dir())

    def _select_installed(self, row: dict) -> None:
        self.game = row["game"]
        self.loader = row["loader"] if row["loader"] in loaders.LOADER_BY_ID else "vanilla"
        self.installed_id = row["id"]
        self.installed_size = row["size"]
        self.cfg["selected_version"] = self.game
        self.cfg["selected_loader"] = self.loader
        paths.save_config(self.cfg)
        self.choices = {item["id"]: {"state": "loading"} for item in loaders.LOADERS}
        self.choices["vanilla"] = {"state": "ok", "version": ""}
        self._show_page("home")
        self._resolve_choices()
        self._detect_java()

    def _home_loader_text(self) -> str:
        if self.installed_id:
            return f"{self._loader_label()} · {self.installed_id}"
        if self.game:
            return f"{self._loader_label()} · не установлена"
        return "версия не выбрана"

    def _draw_home(self) -> None:
        cv = self.canvas
        rows = self._installed_rows()
        if rows and not self.installed_id:
            first = rows[0]
            self.game = first["game"]
            self.loader = first["loader"] if first["loader"] in loaders.LOADER_BY_ID else "vanilla"
            self.installed_id = first["id"]
            self.installed_size = first["size"]
            self.cfg["selected_version"] = self.game
            self.cfg["selected_loader"] = self.loader
            paths.save_config(self.cfg)
        if self.game and self.choices_game != self.game:
            self._resolve_choices()
        self._header("Главная", "Профиль, установленная версия и запуск")

        draw.text(cv, CX0, 126, "УСТАНОВЛЕННЫЕ ВЕРСИИ", 8, theme.DIM, "normal", "w", "page")
        list_x1 = 700
        y = 144

        if not rows:
            draw.rr(cv, CX0, y, list_x1, y + 156, 16, fill=theme.CARD, outline=theme.BORDER, tags="page")
            draw.text(cv, CX0 + 24, y + 32, "Ничего не установлено", 13, theme.TEXT, "bold", "w", "page")
            draw.text(cv, CX0 + 24, y + 58, "Откройте «Версии», выберите сборку и нажмите «Скачать».",
                      8, theme.MUTED, "normal", "w", "page")
            go = CanvasButton(self, CX0 + 24, y + 92, CX0 + 236, y + 132, "Перейти к версиям",
                              command=lambda: self._show_page("play"), kind="primary", size=9)
            self.page_buttons.append(go)
        else:
            for row in rows[:6]:
                selected = row["id"] == self.installed_id
                accent = theme.loader_accent(row["loader"])
                tag = f"home_{row['id']}"
                draw.rr(cv, CX0, y, list_x1, y + 68, 12,
                        fill=theme.SELECT_BG if selected else theme.CARD,
                        outline=accent if selected else theme.BORDER, width=2 if selected else 1,
                        tags=("page", tag))
                draw.cube(cv, CX0 + 34, y + 34, 26, accent, tags=("page", tag))
                draw.text(cv, CX0 + 60, y + 22, f"Minecraft {row['game']}", 12, theme.TEXT,
                          "bold", "w", ("page", tag))
                draw.text(cv, CX0 + 60, y + 44, f"{row['label']} · {paths.human_size(row['size'])}",
                          8, theme.MUTED, "normal", "w", ("page", tag))
                if row["id"] in (self.launching_id, self.running_id):
                    state = "запущена" if row["id"] == self.running_id else "запускаю…"
                    draw.text(cv, list_x1 - 16, y + 34, state, 8, theme.ACCENT, "normal", "e",
                              ("page", tag))
                cv.tag_bind(tag, "<Button-1>", lambda e, r=row: self._select_installed(r))
                cv.tag_bind(tag, "<Enter>", lambda e: cv.configure(cursor="hand2"))
                cv.tag_bind(tag, "<Leave>", lambda e: cv.configure(cursor=""))
                y += 76
            more = CanvasButton(self, CX0, y + 6, CX0 + 214, y + 40, "Все версии и загрузчики",
                                command=lambda: self._show_page("play"), kind="ghost", size=9)
            self.page_buttons.append(more)

        self._draw_home_panel()

    def _draw_home_panel(self) -> None:
        cv = self.canvas
        px0, px1 = 724, 1144
        inner0, inner1 = px0 + 20, px1 - 20
        draw.rr(cv, px0, 116, px1, 640, 18, fill=theme.PANEL, outline=theme.PANEL_BORDER, tags="page")

        draw.text(cv, inner0, 142, "ВЫБРАНО", 8, theme.DIM, "normal", "w", "page")
        draw.text(cv, inner0, 168, f"Minecraft {self.game}" if self.game else "—", 18,
                  theme.TEXT, "bold", "w", "page")
        draw.text(cv, inner0, 194, self._home_loader_text()[:44], 8, theme.MUTED, "normal", "w", "page")

        status_text, status_color = self._status_line()
        draw.dot(cv, inner0 + 4, 216, 3.2, status_color, tags="page")
        draw.text(cv, inner0 + 14, 216, status_text[:42], 8, status_color, "normal", "w", "page")

        draw.hline(cv, inner0, inner1, 240, theme.DIVIDER, tags="page")

        draw.text(cv, inner0, 262, "ПРОФИЛЬ", 8, theme.DIM, "normal", "w", "page")
        profile = profiles_core.active(self.cfg)
        draw.rr(cv, inner0 - 4, 274, inner1 + 4, 330, 12, fill=theme.CARD,
                outline=theme.BORDER, tags="page")
        draw.cube(cv, inner0 + 18, 302, 22, theme.ACCENT, tags="page")
        draw.text(cv, inner0 + 38, 294, str(profile.get("name") or "Профиль"), 10,
                  theme.TEXT, "bold", "w", "page")
        draw.text(cv, inner0 + 38, 312, f"ник: {self._launch_nickname(profile)}", 8,
                  theme.MUTED, "normal", "w", "page")
        change = CanvasButton(self, inner1 - 126, 286, inner1 - 2, 318, "Сменить",
                              command=self._open_profiles_modal, kind="ghost", size=8)
        self.page_buttons.append(change)
        draw.text(cv, inner0, 348, self._account_line(), 8,
                  theme.ACCENT if self._account_mode() != "offline" else theme.DIM,
                  "normal", "w", "page")

        draw.hline(cv, inner0, inner1, 368, theme.DIVIDER, tags="page")

        play = CanvasButton(self, inner0, 384, inner1, 444, "Играть",
                            command=lambda: self._start_game(self.installed_id),
                            kind="primary", size=13, enabled=bool(self.installed_id))
        mid = (inner0 + inner1) // 2
        open_version = CanvasButton(self, inner0, 454, mid - 4, 490, "Папка версии",
                                    command=lambda: self._open_version_folder(self.installed_id or ""),
                                    kind="ghost", size=9, enabled=bool(self.installed_id))
        repair = CanvasButton(self, mid + 4, 454, inner1, 490, "Проверить файлы",
                              command=self._repair_selected, kind="ghost", size=9,
                              enabled=bool(self.installed_id))
        new_profile = CanvasButton(self, inner0, 498, mid - 4, 534, "Профили",
                                   command=self._open_create_profile_modal, kind="ghost", size=9)
        account_btn = CanvasButton(self, mid + 4, 498, inner1, 534, "Войти",
                                   command=self._open_account_modal, kind="ghost", size=9)
        self.page_buttons.extend([play, open_version, repair, new_profile, account_btn])

        draw.hline(cv, inner0, inner1, 552, theme.DIVIDER, tags="page")
        draw.text(cv, inner0, 572, self.log_lines[-1] if self.log_lines else "Готово к запуску",
                  7, theme.DIM, "normal", "w", "page")
        draw.text(cv, inner0, 592, "Моды: папка сборки mods · профили хранятся в настройках лаунчера",
                  7, theme.MUTED, "normal", "w", "page")

    # ------------------------------------------------------------ профили

    def _close_modal(self, rebuild: bool = True) -> None:
        if not self.modal:
            return
        if self.modal == "account":
            self._ms_cancel.set()
            self._ms["busy"] = False
            self._ms["stage"] = ""
        self.modal = None
        self.canvas.delete("modal")
        for button in getattr(self, "modal_buttons", []):
            button.destroy()
        self.modal_buttons = []
        for name in ("modal_name", "modal_nick", "modal_srv_name", "modal_srv_addr",
                     "ely_login", "ely_password", "ely_totp", "mr_query", "pk_query",
                     "skin_nick"):
            entry = self.entries.get(name)
            if entry is not None:
                entry.place_forget()
        if rebuild:
            self._show_page(self.page)
        self._server_edit_id = None

    def _open_profiles_modal(self) -> None:
        self.modal = "profiles"
        self._hide_entries()
        self._draw_modal()

    def _open_create_profile_modal(self) -> None:
        self.modal = "create"
        self._hide_entries()
        for name in ("modal_name", "modal_nick"):
            entry = self.entries.get(name)
            if entry is not None:
                entry.delete(0, "end")
        self._draw_modal()

    def _draw_modal(self) -> None:
        cv = self.canvas
        cv.delete("modal")
        for button in getattr(self, "modal_buttons", []):
            button.destroy()
        self.modal_buttons = []
        px0, px1, py0, py1 = 330, 850, 150, 574
        cv.create_rectangle(0, 0, W, H, fill=theme.OVERLAY, outline="", stipple="gray50",
                            tags=("modal", "modal_bg"))
        cv.tag_bind("modal_bg", "<Button-1>", lambda e: self._close_modal())
        draw.rr(cv, px0, py0, px1, py1, 18, fill=theme.PANEL, outline=theme.BORDER, tags="modal")
        if self.modal == "profiles":
            self._draw_profiles_list(px0, px1, py0, py1)
        elif self.modal == "server":
            self._draw_server_form(px0, px1, py0, py1)
        elif self.modal == "account":
            self._draw_account(px0, px1, py0, py1)
        elif self.modal == "modrinth":
            self._draw_modrinth(px0, px1, py0, py1)
        elif self.modal == "packs":
            self._draw_packs(px0, px1, py0, py1)
        elif self.modal == "updates":
            self._draw_updates(px0, px1, py0, py1)
        elif self.modal == "backups":
            self._draw_backups(px0, px1, py0, py1)
        elif self.modal == "skin":
            self._draw_skin(px0, px1, py0, py1)
        else:
            self._draw_profile_form(px0, px1, py0, py1)

    def _draw_profiles_list(self, px0, px1, py0, py1) -> None:
        cv = self.canvas
        draw.text(cv, px0 + 28, py0 + 34, "Профили", 14, theme.TEXT, "bold", "w", "modal")
        draw.text(cv, px0 + 28, py0 + 56, "Ник профиля применяется при запуске игры",
                  8, theme.MUTED, "normal", "w", "modal")
        close = CanvasButton(self, px1 - 62, py0 + 20, px1 - 24, py0 + 52, "×",
                             command=self._close_modal, kind="ghost", size=12)
        self.modal_buttons.append(close)

        items = profiles_core.profiles(self.cfg)
        y = py0 + 80
        for profile in items[:4]:
            active = profile.get("id") == self.cfg.get("active_profile")
            tag = f"pr_{profile.get('id')}"
            draw.rr(cv, px0 + 24, y, px1 - 24, y + 64, 12,
                    fill=theme.SELECT_BG if active else theme.CARD,
                    outline=theme.ACCENT if active else theme.BORDER, width=2 if active else 1,
                    tags=("modal", tag))
            draw.text(cv, px0 + 44, y + 22, str(profile.get("name") or "Профиль"), 11,
                      theme.TEXT, "bold", "w", ("modal", tag))
            draw.text(cv, px0 + 44, y + 42, f"ник: {profile.get('nickname')}", 8,
                      theme.MUTED, "normal", "w", ("modal", tag))
            if active:
                draw.text(cv, px1 - 96, y + 31, "активен", 8, theme.ACCENT, "normal", "e",
                          ("modal", tag))
            else:
                cv.tag_bind(tag, "<Button-1>", lambda e, pid=profile.get("id"): self._activate_profile(pid))
                cv.tag_bind(tag, "<Enter>", lambda e: cv.configure(cursor="hand2"))
                cv.tag_bind(tag, "<Leave>", lambda e: cv.configure(cursor=""))
            if len(items) > 1:
                remove = CanvasButton(self, px1 - 58, y + 18, px1 - 30, y + 46, "✕",
                                      command=lambda pid=profile.get("id"): self._delete_profile(pid),
                                      kind="ghost", size=9)
                self.modal_buttons.append(remove)
            y += 72

        new_button = CanvasButton(self, px0 + 24, py1 - 58, px0 + 214, py1 - 20,
                                  "Новый профиль", command=self._open_create_profile_modal,
                                  kind="primary", size=9)
        done = CanvasButton(self, px1 - 138, py1 - 58, px1 - 24, py1 - 20, "Готово",
                            command=self._close_modal, kind="ghost", size=9)
        self.modal_buttons.extend([new_button, done])

    def _draw_profile_form(self, px0, px1, py0, py1) -> None:
        cv = self.canvas
        draw.text(cv, px0 + 28, py0 + 34, "Новый профиль", 14, theme.TEXT, "bold", "w", "modal")
        draw.text(cv, px0 + 28, py0 + 58, "Профиль — это свой ник. Удобно для разных серверов.",
                  8, theme.MUTED, "normal", "w", "modal")

        draw.text(cv, px0 + 28, py0 + 92, "Название", 8, theme.DIM, "normal", "w", "modal")
        draw.rr(cv, px0 + 28, py0 + 104, px1 - 28, py0 + 142, 10, fill=theme.FIELD,
                outline=theme.BORDER, tags="modal")
        self.entries.get("modal_name") or self._entry("modal_name", "")
        self._place_entry("modal_name", px0 + 28, py0 + 104, px1 - px0 - 56, 38)
        draw.text(cv, px0 + 28, py0 + 160, "например: основной, сервер, тест", 8, theme.DIM,
                  "normal", "w", "modal")

        draw.text(cv, px0 + 28, py0 + 196, "Ник", 8, theme.DIM, "normal", "w", "modal")
        draw.rr(cv, px0 + 28, py0 + 208, px0 + 288, py0 + 246, 10, fill=theme.FIELD,
                outline=theme.BORDER, tags="modal")
        self.entries.get("modal_nick") or self._entry("modal_nick", "")
        self._place_entry("modal_nick", px0 + 28, py0 + 208, 260, 38)
        draw.text(cv, px0 + 300, py0 + 227, "до 16 символов", 8, theme.DIM, "normal", "w", "modal")

        self.modal_hint = draw.text(cv, px0 + 28, py0 + 272, "", 8, theme.DANGER, "normal", "w", "modal")

        create = CanvasButton(self, px0 + 28, py1 - 62, px0 + 188, py1 - 22, "Создать",
                              command=self._create_profile, kind="primary", size=10)
        cancel = CanvasButton(self, px0 + 200, py1 - 62, px0 + 320, py1 - 22, "Отмена",
                              command=self._close_modal, kind="ghost", size=10)
        self.modal_buttons.extend([create, cancel])

    def _activate_profile(self, profile_id: str) -> None:
        if profiles_core.activate(self.cfg, profile_id):
            paths.save_config(self.cfg)
            self._close_modal()
            self._push_log(f"Профиль: {profiles_core.active(self.cfg).get('name')}")

    def _create_profile(self) -> None:
        name_entry = self.entries.get("modal_name")
        nick_entry = self.entries.get("modal_nick")
        name = (name_entry.get() if name_entry else "").strip()
        nickname = (nick_entry.get() if nick_entry else "").strip()
        if not nickname:
            nickname = name
        if not nickname:
            if hasattr(self, "modal_hint"):
                self.canvas.itemconfigure(self.modal_hint, text="Укажите название или ник")
            return
        profile = profiles_core.create(self.cfg, name or nickname, nickname[:16])
        profiles_core.activate(self.cfg, profile["id"])
        paths.save_config(self.cfg)
        self._close_modal()
        self._push_log(f"Создан профиль {profile['name']} · ник {profile['nickname']}")

    def _delete_profile(self, profile_id: str) -> None:
        if profiles_core.remove(self.cfg, profile_id):
            paths.save_config(self.cfg)
            self._draw_modal()
            self._push_log("Профиль удалён")
        else:
            self._push_log("Нельзя удалить единственный профиль")

    # ------------------------------------------------------------ аккаунт

    def _open_account_modal(self) -> None:
        self.modal = "account"
        self._acct_error = ""
        if not self._ms["busy"]:
            self._ms.update(stage="", code="", uri="")
        self._account_tab = self._account_mode()
        self._hide_entries()
        self._draw_modal()

    def _account_mode(self) -> str:
        """Каким аккаунтом запускать игру: offline, elyby или microsoft."""
        mode = str(self.cfg.get("account_mode") or "offline")
        if mode == "microsoft" and not microsoft_core.connected(self.cfg):
            return "offline"
        if mode == "elyby" and not elyby_core.connected(self.cfg):
            return "offline"
        return mode if mode in ("offline", "elyby", "microsoft") else "offline"

    def _launch_nickname(self, profile: dict) -> str:
        """Ник, с которым реально запустится игра при текущем способе входа."""
        mode = self._account_mode()
        if mode == "microsoft":
            return str(microsoft_core.account(self.cfg).get("profile_name")
                       or profile.get("nickname") or "")
        if mode == "elyby":
            return str(elyby_core.account(self.cfg).get("profile_name")
                       or profile.get("nickname") or "")
        return str(profile.get("nickname") or "")

    def _account_line(self) -> str:
        mode = self._account_mode()
        if mode == "microsoft":
            name = str(microsoft_core.account(self.cfg).get("profile_name") or "")
            return f"Вход: Microsoft · {name}"[:44]
        if mode == "elyby":
            name = str(elyby_core.account(self.cfg).get("profile_name") or "")
            return f"Вход: Ely.by · {name}"[:44]
        return "Вход: офлайн-ник (только offline-mode серверы)"

    def _set_account_tab(self, key: str) -> None:
        self._account_tab = key
        self._acct_error = ""
        self._draw_modal()

    def _set_account_mode(self, mode: str) -> None:
        self.cfg["account_mode"] = mode
        paths.save_config(self.cfg)
        name = {"offline": "офлайн-ник", "elyby": "Ely.by", "microsoft": "Microsoft"}.get(mode, mode)
        self._push_log(f"Вход при запуске: {name}")
        self._draw_modal()

    def _draw_account(self, px0, px1, py0, py1) -> None:
        cv = self.canvas
        self._hide_entries()
        draw.text(cv, px0 + 28, py0 + 34, "Аккаунт", 14, theme.TEXT, "bold", "w", "modal")
        draw.text(cv, px0 + 28, py0 + 56,
                  "Способ входа выбирается здесь и применяется при запуске",
                  8, theme.MUTED, "normal", "w", "modal")
        close = CanvasButton(self, px1 - 62, py0 + 20, px1 - 24, py0 + 52, "×",
                             command=self._close_modal, kind="ghost", size=12)
        self.modal_buttons.append(close)

        tabs = (("offline", "Офлайн-ник"), ("elyby", "Ely.by"), ("microsoft", "Microsoft"))
        left, right = px0 + 28, px1 - 28
        width = (right - left - 16) // 3
        for index, (key, title) in enumerate(tabs):
            bx = left + index * (width + 8)
            button = CanvasButton(
                self, bx, py0 + 82, bx + width, py0 + 118, title,
                command=lambda k=key: self._set_account_tab(k),
                kind="primary" if self._account_tab == key else "ghost", size=9,
            )
            self.modal_buttons.append(button)
        draw.hline(cv, left, right, py0 + 132, theme.DIVIDER, tags="modal")

        if self._account_tab == "offline":
            self._draw_account_offline(px0, px1, py0, py1)
        elif self._account_tab == "elyby":
            self._draw_account_ely(px0, px1, py0, py1)
        else:
            self._draw_account_ms(px0, px1, py0, py1)

        done = CanvasButton(self, px1 - 138, py1 - 58, px1 - 24, py1 - 20, "Готово",
                            command=self._close_modal, kind="ghost", size=9)
        self.modal_buttons.append(done)

    def _draw_account_offline(self, px0, px1, py0, py1) -> None:
        cv = self.canvas
        left = px0 + 28
        profile = profiles_core.active(self.cfg)
        nickname = str(profile.get("nickname") or self.cfg.get("nickname") or "Steve")
        active = self._account_mode() == "offline"
        draw.text(cv, left, py0 + 164, "Играть без входа", 12, theme.TEXT, "bold", "w", "modal")
        draw.text(cv, left, py0 + 190,
                  f"ник из профиля «{profile.get('name')}»: {nickname}", 9, theme.MUTED,
                  "normal", "w", "modal")
        draw.text(cv, left, py0 + 224, "Подходит для одиночной игры, локалки и серверов", 8,
                  theme.DIM, "normal", "w", "modal")
        draw.text(cv, left, py0 + 242, "с online-mode=false. Официальные серверы не пустят.", 8,
                  theme.DIM, "normal", "w", "modal")
        use = CanvasButton(self, left, py0 + 322, left + 210, py0 + 360,
                           "Офлайн-ник активен" if active else "Использовать офлайн-ник",
                           command=lambda: self._set_account_mode("offline"),
                           kind="ghost" if active else "primary", size=9, enabled=not active)
        profiles_btn = CanvasButton(self, left + 222, py0 + 322, left + 352, py0 + 360,
                                    "Профили", command=self._open_profiles_modal,
                                    kind="ghost", size=9)
        self.modal_buttons.extend([use, profiles_btn])
        draw.text(cv, left, py0 + 376, "Ник профиля подставляется в запуск без интернета.", 8,
                  theme.DIM, "normal", "w", "modal")

    def _draw_account_ely(self, px0, px1, py0, py1) -> None:
        cv = self.canvas
        left, right = px0 + 28, px1 - 28
        stored = elyby_core.account(self.cfg)
        if elyby_core.connected(self.cfg):
            active = self._account_mode() == "elyby"
            draw.rr(cv, left, py0 + 152, right, py0 + 226, 12, fill=theme.CARD,
                    outline=theme.BORDER, tags="modal")
            draw.text(cv, left + 18, py0 + 174,
                      f"Ely.by · {stored.get('profile_name')}", 12, theme.TEXT, "bold",
                      "w", "modal")
            draw.text(cv, left + 18, py0 + 196,
                      "вход сохранён · скины и плащи подтянутся сами", 8, theme.MUTED,
                      "normal", "w", "modal")
            use = CanvasButton(self, left, py0 + 244, left + 214, py0 + 282,
                               "Ely.by активен" if active else "Использовать Ely.by",
                               command=lambda: self._set_account_mode("elyby"),
                               kind="ghost" if active else "primary", size=9, enabled=not active)
            out = CanvasButton(self, left + 226, py0 + 244, left + 336, py0 + 282,
                               "Выйти", command=self._ely_logout, kind="ghost", size=9)
            self.modal_buttons.extend([use, out])
            draw.text(cv, left, py0 + 302,
                      "Пустят серверы с Ely.by (authlib-injector) и offline-mode.", 8,
                      theme.DIM, "normal", "w", "modal")
            draw.text(cv, left, py0 + 320,
                      "Официальные серверы Mojang требуют вход Microsoft.", 8, theme.DIM,
                      "normal", "w", "modal")
            return

        draw.text(cv, left, py0 + 156, "Логин или e-mail", 8, theme.DIM, "normal", "w", "modal")
        draw.rr(cv, left, py0 + 168, right, py0 + 206, 10, fill=theme.FIELD,
                outline=theme.BORDER, tags="modal")
        self.entries.get("ely_login") or self._entry("ely_login", "")
        self._place_entry("ely_login", left, py0 + 168, right - left, 38)

        draw.text(cv, left, py0 + 218, "Пароль", 8, theme.DIM, "normal", "w", "modal")
        draw.rr(cv, left, py0 + 230, right, py0 + 268, 10, fill=theme.FIELD,
                outline=theme.BORDER, tags="modal")
        self.entries.get("ely_password") or self._entry("ely_password", "", secret=True)
        self._place_entry("ely_password", left, py0 + 230, right - left, 38)

        if self._ely_stage == "2fa":
            draw.text(cv, left, py0 + 280, "Код 2FA из приложения", 8, theme.DIM,
                      "normal", "w", "modal")
            draw.rr(cv, left, py0 + 292, right, py0 + 330, 10, fill=theme.FIELD,
                    outline=theme.BORDER, tags="modal")
            self.entries.get("ely_totp") or self._entry("ely_totp", "")
            self._place_entry("ely_totp", left, py0 + 292, right - left, 38)
            hint_y = py0 + 348
        else:
            hint_y = py0 + 286

        if self._acct_error:
            draw.text(cv, left, hint_y, self._acct_error[:70], 8, theme.DANGER,
                      "normal", "w", "modal")
        elif self._ely_stage == "2fa":
            draw.text(cv, left, hint_y, "Код добавляется к паролю через «:»", 8, theme.DIM,
                      "normal", "w", "modal")
        else:
            draw.text(cv, left, hint_y, "Аккаунт Ely.by — скины и серверы с Ely.by", 8,
                      theme.DIM, "normal", "w", "modal")

        login = CanvasButton(self, left, py0 + 368, left + 150, py0 + 406,
                             "Проверяю…" if self._ely_busy else "Войти",
                             command=self._ely_submit, kind="primary", size=9,
                             enabled=not self._ely_busy)
        profiles_btn = CanvasButton(self, left + 162, py0 + 368, left + 292, py0 + 406,
                                    "Профили", command=self._open_profiles_modal,
                                    kind="ghost", size=9)
        self.modal_buttons.extend([login, profiles_btn])

    def _draw_account_ms(self, px0, px1, py0, py1) -> None:
        cv = self.canvas
        left, right = px0 + 28, px1 - 28
        stored = microsoft_core.account(self.cfg)
        if microsoft_core.connected(self.cfg):
            active = self._account_mode() == "microsoft"
            draw.rr(cv, left, py0 + 152, right, py0 + 226, 12, fill=theme.CARD,
                    outline=theme.BORDER, tags="modal")
            draw.text(cv, left + 18, py0 + 174,
                      f"Microsoft · {stored.get('profile_name')}", 12, theme.TEXT, "bold",
                      "w", "modal")
            draw.text(cv, left + 18, py0 + 196,
                      "официальный вход · серверы online-mode доступны", 8, theme.MUTED,
                      "normal", "w", "modal")
            use = CanvasButton(self, left, py0 + 244, left + 224, py0 + 282,
                               "Microsoft активен" if active else "Использовать Microsoft",
                               command=lambda: self._set_account_mode("microsoft"),
                               kind="ghost" if active else "primary", size=9, enabled=not active)
            out = CanvasButton(self, left + 236, py0 + 244, left + 346, py0 + 282,
                               "Выйти", command=self._ms_logout, kind="ghost", size=9)
            self.modal_buttons.extend([use, out])
            draw.text(cv, left, py0 + 302,
                      "Токен обновляется сам — вход переживёт перезапуск.", 8, theme.DIM,
                      "normal", "w", "modal")
            draw.text(cv, left, py0 + 320,
                      "Выход удаляет сохранённый вход с этого ПК.", 8, theme.DIM,
                      "normal", "w", "modal")
            return

        if self._ms["code"]:
            draw.text(cv, left, py0 + 158, "Код для входа", 8, theme.DIM, "normal", "w", "modal")
            draw.text(cv, left, py0 + 186, self._ms["code"], 26, theme.ACCENT, "bold",
                      "w", "modal")
            draw.text(cv, left, py0 + 232,
                      f"Откройте {self._short_uri()} и введите код", 9, theme.TEXT,
                      "normal", "w", "modal")
            open_btn = CanvasButton(self, left, py0 + 254, left + 236, py0 + 292,
                                    "Открыть страницу входа", command=self._ms_open_uri,
                                    kind="primary", size=9)
            self.modal_buttons.append(open_btn)
            status = ("Завершаю вход…" if self._ms["stage"] == "finishing"
                      else "Ожидаю подтверждения в браузере…")
            draw.text(cv, left, py0 + 312, status, 8, theme.ACCENT, "normal", "w", "modal")
            draw.text(cv, left, py0 + 330, "Код действует 15 минут.", 8, theme.DIM,
                      "normal", "w", "modal")
            cancel = CanvasButton(self, left, py0 + 368, left + 150, py0 + 406, "Отмена",
                                  command=self._ms_cancel_login, kind="ghost", size=9)
            self.modal_buttons.append(cancel)
            return

        draw.text(cv, left, py0 + 162, "Официальный вход, как в лаунчере Mojang", 12,
                  theme.TEXT, "bold", "w", "modal")
        draw.text(cv, left, py0 + 192,
                  "Код подтверждается в браузере — пароль лаунчер не видит.", 8,
                  theme.MUTED, "normal", "w", "modal")
        draw.text(cv, left, py0 + 214,
                  "После входа работают серверы с online-mode, ваши скины", 8,
                  theme.MUTED, "normal", "w", "modal")
        draw.text(cv, left, py0 + 232, "и прогресс лицензионного аккаунта.", 8,
                  theme.MUTED, "normal", "w", "modal")
        draw.text(cv, left, py0 + 268,
                  "Нужен аккаунт Microsoft с купленным Minecraft: Java Edition.", 8,
                  theme.DIM, "normal", "w", "modal")
        if self._acct_error:
            draw.text(cv, left, py0 + 296, self._acct_error[:70], 8, theme.DANGER,
                      "normal", "w", "modal")
        start = CanvasButton(self, left, py0 + 320, left + 236, py0 + 358,
                             "Войти через Microsoft", command=self._ms_start,
                             kind="primary", size=9, enabled=not self._ms["busy"])
        self.modal_buttons.append(start)
        draw.text(cv, left, py0 + 372, "Откроется microsoft.com/link — это безопасно.", 8,
                  theme.DIM, "normal", "w", "modal")

    def _short_uri(self) -> str:
        uri = str(self._ms.get("uri") or "https://www.microsoft.com/link")
        return uri.replace("https://", "").replace("http://", "").rstrip("/")

    # -------------------------------------------------------- Ely.by вход

    def _ely_submit(self) -> None:
        if self._ely_busy:
            return
        login_entry = self.entries.get("ely_login")
        password_entry = self.entries.get("ely_password")
        totp_entry = self.entries.get("ely_totp")
        login = (login_entry.get() if login_entry else "").strip()
        password = (password_entry.get() if password_entry else "").strip()
        totp = (totp_entry.get() if totp_entry else "").strip() if self._ely_stage == "2fa" else ""
        if not login or not password:
            self._acct_error = "Укажите логин и пароль"
            self._draw_modal()
            return
        self._ely_busy = True
        self._acct_error = ""
        self._draw_modal()
        secret = f"{password}:{totp}" if totp else password

        def work():
            try:
                auth = elyby_core.authenticate(login, secret, elyby_core.new_client_token())
            except elyby_core.TwoFactorError:
                self._post(self._ely_need_2fa)
                return
            except elyby_core.AuthError as exc:
                self._post(lambda message=str(exc): self._ely_failed(message))
                return
            self._post(lambda: self._ely_done(auth, login, password))

        threading.Thread(target=work, daemon=True).start()

    def _ely_need_2fa(self) -> None:
        self._ely_busy = False
        self._ely_stage = "2fa"
        self._acct_error = "У аккаунта включён 2FA — введите код"
        if self.modal == "account" and self._account_tab == "elyby":
            self._draw_modal()

    def _ely_failed(self, message: str) -> None:
        self._ely_busy = False
        self._acct_error = message
        if self.modal == "account" and self._account_tab == "elyby":
            self._draw_modal()

    def _ely_done(self, auth: dict, login: str, password: str) -> None:
        self._ely_busy = False
        self._ely_stage = ""
        self._acct_error = ""
        stored = elyby_core.apply_auth(self.cfg, auth, login, password)
        self.cfg["account_mode"] = "elyby"
        paths.save_config(self.cfg)
        self._account_tab = "elyby"
        self._push_log(f"Ely.by: {stored.get('profile_name')}")
        if self.modal == "account":
            self._draw_modal()
        elif self.page == "home":
            self._show_page("home")

    def _ely_logout(self) -> None:
        def work():
            elyby_core.logout(self.cfg)
            if str(self.cfg.get("account_mode") or "") == "elyby":
                self.cfg["account_mode"] = "offline"
            paths.save_config(self.cfg)
            self._post(self._ely_logged_out)

        threading.Thread(target=work, daemon=True).start()

    def _ely_logged_out(self) -> None:
        self._ely_stage = ""
        self._acct_error = ""
        self._push_log("Вход Ely.by удалён")
        if self.modal == "account":
            self._draw_modal()
        elif self.page == "home":
            self._show_page("home")

    # ----------------------------------------------------- Microsoft вход

    def _ms_start(self) -> None:
        if self._ms["busy"]:
            return
        self._ms_cancel = threading.Event()
        self._ms = {"stage": "starting", "busy": True, "code": "", "uri": "", "opened": False}
        self._acct_error = ""
        self._draw_modal()

        def work():
            try:
                info = microsoft_core.start_device_code()
            except microsoft_core.AuthError as exc:
                self._post(lambda message=str(exc): self._ms_failed(message))
                return
            self._post(lambda: self._ms_code_ready(info))
            try:
                tokens = microsoft_core.poll_device_code(
                    info["device_code"], info.get("interval", 5),
                    info.get("expires_in", 900), cancel=self._ms_cancel,
                )
            except microsoft_core.AuthError as exc:
                self._post(lambda message=str(exc): self._ms_failed(message))
                return
            if tokens is None:
                return
            self._post(self._ms_finishing)
            try:
                data = microsoft_core.complete_login(tokens)
            except microsoft_core.AuthError as exc:
                self._post(lambda message=str(exc): self._ms_failed(message))
                return
            self._post(lambda: self._ms_done(data))

        threading.Thread(target=work, daemon=True).start()

    def _ms_code_ready(self, info: dict) -> None:
        if self._ms.get("stage") not in ("starting", "waiting"):
            return
        self._ms.update(stage="waiting", code=str(info.get("user_code") or ""),
                        uri=str(info.get("verification_uri") or ""))
        self._push_log(f"Microsoft: код {self._ms['code'] or '—'} на {self._short_uri()}")
        if self.modal == "account" and self._account_tab == "microsoft":
            self._draw_modal()
        self._ms_open_uri()

    def _ms_open_uri(self) -> None:
        uri = str(self._ms.get("uri") or "https://www.microsoft.com/link")
        try:
            webbrowser.open(uri)
        except Exception:  # noqa: BLE001 - нет браузера — код можно ввести вручную
            pass

    def _ms_finishing(self) -> None:
        self._ms["stage"] = "finishing"
        if self.modal == "account" and self._account_tab == "microsoft":
            self._draw_modal()

    def _ms_failed(self, message: str) -> None:
        self._ms.update(stage="", busy=False, code="", uri="")
        self._acct_error = message
        self._push_log(f"Вход Microsoft: {message}")
        if self.modal == "account" and self._account_tab == "microsoft":
            self._draw_modal()

    def _ms_cancel_login(self) -> None:
        self._ms_cancel.set()
        self._ms.update(stage="", busy=False, code="", uri="")
        self._acct_error = "Вход отменён"
        if self.modal == "account":
            self._draw_modal()

    def _ms_done(self, data: dict) -> None:
        self._ms = {"stage": "", "busy": False, "code": "", "uri": "", "opened": False}
        self._acct_error = ""
        microsoft_core.apply(self.cfg, data)
        self.cfg["account_mode"] = "microsoft"
        paths.save_config(self.cfg)
        self._account_tab = "microsoft"
        self._push_log(f"Microsoft: {data.get('profile_name')}")
        if self.modal == "account":
            self._draw_modal()
        elif self.page == "home":
            self._show_page("home")

    def _ms_logout(self) -> None:
        microsoft_core.logout(self.cfg)
        paths.save_config(self.cfg)
        self._ms = {"stage": "", "busy": False, "code": "", "uri": "", "opened": False}
        self._acct_error = ""
        self._push_log("Вход Microsoft удалён")
        if self.modal == "account":
            self._draw_modal()
        elif self.page == "home":
            self._show_page("home")

    # -------------------------------------------------------- сетевая игра

    def _draw_servers(self):
        cv = self.canvas
        self._header("Сетевая игра", "Серверы с пингом · вход в один клик · игра по локалке")
        items = servers_core.servers(self.cfg)

        add_btn = CanvasButton(self, CX0, SEARCH_Y0, CX0 + 150, SEARCH_Y1, "Добавить сервер",
                               command=lambda: self._open_server_modal(), kind="primary", size=9)
        refresh_btn = CanvasButton(self, CX0 + 162, SEARCH_Y0, CX0 + 296, SEARCH_Y1, "Обновить пинг",
                                   command=lambda: self._servers_ping_all(), kind="ghost", size=9)
        self.page_buttons.extend([add_btn, refresh_btn])
        draw.text(cv, CX1, (SEARCH_Y0 + SEARCH_Y1) / 2,
                  f"ваш IP для друзей: {servers_core.local_ip()} · порт 25565",
                  8, theme.DIM, "normal", "e", "page")

        if not items:
            draw.rr(cv, CX0, 172, CX1, 336, 16, fill=theme.CARD, outline=theme.BORDER, tags="page")
            draw.text(cv, CX0 + 28, 208, "Серверов пока нет", 13, theme.TEXT, "bold", "w", "page")
            draw.text(cv, CX0 + 28, 236, "Добавьте адрес — лаунчер покажет онлайн, MOTD и пинг,",
                      9, theme.MUTED, "normal", "w", "page")
            draw.text(cv, CX0 + 28, 256, "а кнопка «Играть» запустит Minecraft и сразу подключит.",
                      9, theme.MUTED, "normal", "w", "page")
            draw.text(cv, CX0 + 28, 292, "Для игры по локальной сети друг добавляет ваш IP",
                      9, theme.DIM, "normal", "w", "page")
            draw.text(cv, CX0 + 28, 312, "и порт, который игра пишет в чате при «Открыть для сети».",
                      9, theme.DIM, "normal", "w", "page")
            return

        y = 172
        for item in items[:6]:
            sid = str(item.get("id") or "")
            ping = self.servers_pings.get(sid) or {}
            ok = ping.get("ok")
            status = str(ping.get("error") or "")

            draw.rr(cv, CX0, y, CX1, y + 76, 14, fill=theme.CARD, outline=theme.BORDER, tags="page")
            accent = theme.ACCENT if ok else (theme.DANGER if ok is False else theme.DIM)
            draw.cube(cv, CX0 + 40, y + 38, 26, accent, tags="page")
            draw.text(cv, CX0 + 68, y + 24, str(item.get("name") or "Сервер"), 12, theme.TEXT,
                      "bold", "w", "page")
            draw.text(cv, CX0 + 68, y + 46, str(item.get("address") or ""), 9, theme.MUTED,
                      "normal", "w", "page")

            if ok:
                latency = int(ping.get("latency_ms") or 0)
                dot_color = "#4ADE80" if latency < 120 else (theme.WARNING if latency < 350 else theme.DANGER)
                draw.dot(cv, CX0 + 400, y + 26, 3.2, dot_color, tags="page")
                draw.text(cv, CX0 + 410, y + 26,
                          f"{ping.get('version', '')} · {ping.get('online', 0)}/{ping.get('max', 0)} · {latency} мс",
                          8, theme.TEXT_SOFT, "normal", "w", "page")
                motd = str(ping.get("motd") or "")[:48]
                if motd:
                    draw.text(cv, CX0 + 410, y + 50, motd, 8, theme.MUTED, "normal", "w", "page")
            elif status:
                draw.dot(cv, CX0 + 400, y + 26, 3.2, theme.DANGER, tags="page")
                draw.text(cv, CX0 + 410, y + 26, status[:44], 8, theme.DANGER, "normal", "w", "page")
            else:
                draw.text(cv, CX0 + 410, y + 26, "пингую…", 8, theme.MUTED, "normal", "w", "page")

            play_btn = CanvasButton(self, CX1 - 352, y + 18, CX1 - 250, y + 58, "Играть",
                                    command=lambda it=item: self._start_game(self.installed_id,
                                                                             server_address=str(it.get("address") or "")),
                                    kind="primary", size=9, enabled=bool(self.installed_id))
            edit_btn = CanvasButton(self, CX1 - 238, y + 18, CX1 - 132, y + 58, "Изменить",
                                    command=lambda it=item: self._open_server_modal(it), kind="ghost", size=9)
            label = "Точно?" if self._confirm_delete == sid else "Удалить"
            del_btn = CanvasButton(self, CX1 - 120, y + 18, CX1 - 24, y + 58, label,
                                   command=lambda s=sid: self._delete_server(s), kind="danger", size=9)
            self.page_buttons.extend([play_btn, edit_btn, del_btn])
            y += 84

        draw.text(cv, CX0, y + 12,
                  "Список синхронизируется с игрой: экран «Сетевая игра» в Minecraft покажет те же серверы",
                  8, theme.DIM, "normal", "w", "page")

        pending = [it for it in items if str(it.get("id") or "") not in self.servers_pings
                   and str(it.get("id") or "") not in self._ping_busy]
        if pending:
            self._servers_ping_all()

    def _servers_ping_all(self):
        started = False
        for item in servers_core.servers(self.cfg):
            sid = str(item.get("id") or "")
            address = str(item.get("address") or "")
            if not sid or not address or sid in self._ping_busy:
                continue
            self._ping_busy.add(sid)
            started = True

            def work(target_id=sid, target_address=address):
                result = servers_core.ping(target_address, timeout=4)
                self._post(lambda: self._servers_ping_done(target_id, result))

            threading.Thread(target=work, daemon=True).start()
        if started and self.page == "servers":
            self._show_page("servers")

    def _servers_ping_done(self, sid: str, result: dict):
        self._ping_busy.discard(sid)
        self.servers_pings[sid] = result
        if self.page == "servers":
            self._show_page("servers")

    def _open_server_modal(self, item: dict | None = None) -> None:
        self.modal = "server"
        self._server_edit_id = str((item or {}).get("id") or "") or None
        values = {"modal_srv_name": str((item or {}).get("name") or ""),
                  "modal_srv_addr": str((item or {}).get("address") or "")}
        for key, value in values.items():
            entry = self.entries.get(key)
            if entry is None:
                self._entry(key, value)
            else:
                entry.delete(0, "end")
                entry.insert(0, value)
        self._hide_entries()
        self._draw_modal()

    def _draw_server_form(self, px0, px1, py0, py1) -> None:
        cv = self.canvas
        editing = bool(self._server_edit_id)
        draw.text(cv, px0 + 28, py0 + 34, "Изменить сервер" if editing else "Новый сервер",
                  14, theme.TEXT, "bold", "w", "modal")
        draw.text(cv, px0 + 28, py0 + 58, "Кнопка «Играть» запустит выбранную версию и подключится к серверу.",
                  8, theme.MUTED, "normal", "w", "modal")

        draw.text(cv, px0 + 28, py0 + 92, "Название", 8, theme.DIM, "normal", "w", "modal")
        draw.rr(cv, px0 + 28, py0 + 104, px1 - 28, py0 + 142, 10, fill=theme.FIELD,
                outline=theme.BORDER, tags="modal")
        self._place_entry("modal_srv_name", px0 + 28, py0 + 104, px1 - px0 - 56, 38)

        draw.text(cv, px0 + 28, py0 + 160, "Адрес", 8, theme.DIM, "normal", "w", "modal")
        draw.rr(cv, px0 + 28, py0 + 172, px1 - 28, py0 + 210, 10, fill=theme.FIELD,
                outline=theme.BORDER, tags="modal")
        self._place_entry("modal_srv_addr", px0 + 28, py0 + 172, px1 - px0 - 56, 38)
        draw.text(cv, px0 + 28, py0 + 226, "например: mc.example.com · play.net:25577 · 192.168.1.5:25565",
                  8, theme.DIM, "normal", "w", "modal")

        self.modal_hint = draw.text(cv, px0 + 28, py0 + 258, "", 8, theme.DANGER, "normal", "w", "modal")

        save = CanvasButton(self, px0 + 28, py1 - 62, px0 + 188, py1 - 22,
                            "Сохранить" if editing else "Добавить",
                            command=self._save_server, kind="primary", size=10)
        cancel = CanvasButton(self, px0 + 200, py1 - 62, px0 + 320, py1 - 22, "Отмена",
                              command=self._close_modal, kind="ghost", size=10)
        self.modal_buttons.extend([save, cancel])

    def _save_server(self) -> None:
        name_entry = self.entries.get("modal_srv_name")
        addr_entry = self.entries.get("modal_srv_addr")
        name = (name_entry.get() if name_entry else "").strip()
        address = (addr_entry.get() if addr_entry else "").strip()
        if not servers_core.parse_address(address):
            if hasattr(self, "modal_hint"):
                self.canvas.itemconfigure(self.modal_hint, text="Адрес: host или host:port")
            return
        if self._server_edit_id:
            servers_core.update(self.cfg, self._server_edit_id, name=name, address=address)
            self._push_log(f"Сервер обновлён: {address}")
        else:
            created = servers_core.create(self.cfg, name, address)
            if created:
                self.servers_pings.pop(created["id"], None)
            self._push_log(f"Сервер добавлен: {address}")
        paths.save_config(self.cfg)
        self._close_modal()
        if self.page == "servers":
            self._show_page("servers")
            self._servers_ping_all()

    def _delete_server(self, sid: str) -> None:
        if self._confirm_delete == sid:
            self._confirm_delete = None
            if servers_core.remove(self.cfg, sid):
                self.servers_pings.pop(sid, None)
                paths.save_config(self.cfg)
                self._push_log("Сервер удалён")
            self._show_page("servers")
        else:
            self._confirm_delete = sid
            self._show_page("servers")

    # ------------------------------------------------------------ тема

    def _set_theme(self, name: str) -> None:
        self.cfg["theme"] = theme.apply(name)
        paths.save_config(self.cfg)
        self.configure(bg=theme.BG_TOP)
        self._rebuild_all()

    def _set_language(self, name: str) -> None:
        self.cfg["language"] = i18n.set_language(name)
        paths.save_config(self.cfg)
        self._rebuild_all()
        self._push_log("Language: English" if i18n.CURRENT == "en" else "Язык: русский")

    def _rebuild_all(self) -> None:
        self.canvas.delete("all")
        self._hide_entries()
        for button in getattr(self, "page_buttons", []):
            button.destroy()
        self.page_buttons = []
        self.modal_buttons = []
        self._draw_background()
        self._draw_titlebar()
        self._draw_sidebar()
        self._show_page(self.page)

    # ------------------------------------------------------------------- моды

    def _draw_mods(self):
        cv = self.canvas
        data_dir = self._data_dir()
        instance_id = self.installed_id
        _instance_game, instance_loader = self._instance_meta()
        if instance_id:
            try:
                instances_core.ensure(data_dir, instance_id)
            except OSError:
                pass
        items = mods_core.list_mods(data_dir, instance_id) if instance_id else []
        self._mods_signature = mods_core.signature(items)
        enabled_count = sum(1 for item in items if item["enabled"])
        self._header("Моды", f"Включено {enabled_count} из {len(items)} · файлы .jar из папки mods")

        draw.rr(cv, CX0, 116, CX1, 158, 12, fill=theme.CARD, outline=theme.BORDER, tags="page")
        draw.text(cv, CX0 + 18, 132, "Папка модов", 8, theme.DIM, "normal", "w", "page")
        hint = mods_core.folder_hint(data_dir, instance_id) if instance_id else "—"
        draw.text(cv, CX0 + 18, 147, hint[:80], 8, theme.MUTED,
                  "normal", "w", "page")

        tools = [
            ("Modrinth", self._open_modrinth_modal, "primary",
             bool(instance_id and instance_loader != "vanilla")),
            ("Сборка", self._import_modpack, "ghost", bool(instance_id)),
            ("Обновления", self._open_updates_modal, "ghost",
             bool(instance_id and instance_loader != "vanilla")),
            ("Паки", self._open_packs_modal, "ghost", bool(instance_id)),
            ("Бэкапы", self._open_backups_modal, "ghost", bool(instance_id)),
            ("Добавить", self._add_mods, "ghost", bool(instance_id)),
        ]
        tool_x = CX0
        for title, command, kind, enabled in tools:
            button = CanvasButton(self, tool_x, 166, tool_x + 136, 198, title,
                                  command=command, kind=kind, size=8, enabled=enabled)
            self.page_buttons.append(button)
            tool_x += 144

        y = 212
        if not instance_id:
            draw.rr(cv, CX0, y, CX1, y + 112, 16, fill=theme.CARD, outline=theme.BORDER, tags="page")
            draw.text(cv, CX0 + 28, y + 36, "Сначала установите версию с загрузчиком.",
                      12, theme.TEXT, "bold", "w", "page")
            draw.text(cv, CX0 + 28, y + 64,
                      "У каждой сборки своя папка mods, saves и config. Выберите установленную "
                      "версию на странице «Библиотека».",
                      8, theme.MUTED, "normal", "w", "page")
            return

        if instance_loader == "vanilla":
            draw.rr(cv, CX0, y, CX1, y + 38, 10, fill=theme.WARNING_BG, outline=theme.WARNING_BORDER, tags="page")
            draw.text(cv, CX0 + 16, y + 19,
                      "Выбран Vanilla — моды не загрузятся. Переключите загрузчик на Fabric, Forge "
                      "или NeoForge на странице «Играть».",
                      8, theme.WARNING, "normal", "w", "page")
            y += 50

        if not items:
            draw.rr(cv, CX0, y, CX1, y + 112, 16, fill=theme.CARD, outline=theme.BORDER, tags="page")
            draw.text(cv, CX0 + 28, y + 36, "Модов пока нет", 12, theme.TEXT, "bold", "w", "page")
            draw.text(cv, CX0 + 28, y + 62,
                      "Нажмите «Добавить» — откроется папка mods. Перекиньте туда .jar и вернитесь: "
                      "список обновится сам.",
                      8, theme.MUTED, "normal", "w", "page")
            draw.text(cv, CX0 + 28, y + 86,
                      "Моды работают только с загрузчиком и только для подходящей версии игры.",
                      8, theme.DIM, "normal", "w", "page")
            return

        per_page = 4
        pages = max(1, (len(items) + per_page - 1) // per_page)
        self.mods_offset = min(self.mods_offset, pages - 1)
        chunk = items[self.mods_offset * per_page:(self.mods_offset + 1) * per_page]

        for item in chunk:
            enabled = item["enabled"]
            accent = theme.ACCENT if enabled else theme.BORDER_HOVER
            draw.rr(cv, CX0, y, CX1, y + 88, 16, fill=theme.CARD, outline=theme.BORDER, tags="page")
            draw.cube(cv, CX0 + 42, y + 44, 30, accent if enabled else theme.CUBE_OFF, tags="page")
            name = item["name"]
            if len(name) > 52:
                name = name[:49] + "…"
            draw.text(cv, CX0 + 74, y + 24, name, 11, theme.TEXT, "bold", "w", "page")
            draw.dot(cv, CX0 + 78, y + 48, 3, theme.ACCENT if enabled else theme.DIM, tags="page")
            draw.text(cv, CX0 + 88, y + 48,
                      ("включён" if enabled else "отключён") + f" · {paths.human_size(item['size'])}",
                      8, theme.ACCENT if enabled else theme.MUTED, "normal", "w", "page")
            folder_name = mods_core.ENABLED_DIR if enabled else mods_core.DISABLED_DIR
            draw.text(cv, CX0 + 74, y + 66, f"папка {folder_name}", 7, theme.DIM, "normal", "w", "page")

            button = CanvasButton(
                self, CX1 - 184, y + 24, CX1 - 24, y + 64,
                "Отключить" if enabled else "Включить",
                command=lambda n=item["name"], e=enabled: self._toggle_mod(n, not e),
                kind="ghost" if enabled else "primary", size=9,
            )
            self.page_buttons.append(button)
            y += 96

        draw.text(cv, CX0, y + 12, "Отключённые моды переносятся в папку mods-disabled",
                  8, theme.DIM, "normal", "w", "page")
        if pages > 1:
            draw.text(cv, CX1 - 90, y + 12, f"стр. {self.mods_offset + 1} / {pages}", 8,
                      theme.MUTED, "normal", "e", "page")
            prev = CanvasButton(self, CX1 - 84, y - 6, CX1 - 54, y + 24, "←",
                                command=lambda: self._mods_go(-1), kind="ghost", size=9,
                                enabled=self.mods_offset > 0)
            nxt = CanvasButton(self, CX1 - 50, y - 6, CX1 - 20, y + 24, "→",
                               command=lambda: self._mods_go(1), kind="ghost", size=9,
                               enabled=self.mods_offset < pages - 1)
            self.page_buttons.extend([prev, nxt])

    def _mods_go(self, delta):
        self.mods_offset = max(0, self.mods_offset + delta)
        self._show_page("mods")

    def _toggle_mod(self, name, enabled):
        if mods_core.set_enabled(self._data_dir(), name, enabled, self.installed_id):
            self._push_log(f"{'Включён' if enabled else 'Отключён'} мод {name}")
        else:
            self._push_log(f"Не удалось переключить {name}")
        self.mods_offset = 0
        self._show_page("mods")

    def _add_mods(self):
        if not self.installed_id:
            self._push_log("Сначала установите версию с загрузчиком.")
            return
        folder = mods_core.mods_dir(self._data_dir(), self.installed_id)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))  # noqa: S606 - открытие проводника
        except OSError:
            self._push_log("Проводник не открылся")
        self._push_log("Скопируйте .jar в папку mods — список обновится сам")

    # --------------------------------------------------------- Modrinth

    def _open_modrinth_modal(self) -> None:
        if not self.installed_id:
            self._push_log("Сначала установите версию с загрузчиком.")
            return
        _game, loader = self._instance_meta()
        if loader not in ("fabric", "forge", "neoforge"):
            self._push_log("Моды Modrinth доступны для Fabric, Forge или NeoForge")
            return
        self.modal = "modrinth"
        self._hide_entries()
        self._draw_modal()
        if not self._mr["results"] and not self._mr["busy"]:
            self._modrinth_search()

    def _draw_modrinth(self, px0, px1, py0, py1) -> None:
        cv = self.canvas
        game, loader = self._instance_meta()
        draw.text(cv, px0 + 28, py0 + 34, "Поиск в Modrinth", 14, theme.TEXT, "bold", "w", "modal")
        draw.text(cv, px0 + 28, py0 + 56,
                  f"{game} · {loader} · {self.installed_id or ''}", 8, theme.MUTED,
                  "normal", "w", "modal")
        close = CanvasButton(self, px1 - 62, py0 + 20, px1 - 24, py0 + 52, "×",
                             command=self._close_modal, kind="ghost", size=12)
        self.modal_buttons.append(close)

        field_y = py0 + 76
        draw.rr(cv, px0 + 28, field_y, px1 - 150, field_y + 38, 10, fill=theme.FIELD,
                outline=theme.BORDER, tags="modal")
        self.entries.get("mr_query") or self._entry("mr_query", str(self._mr.get("query") or ""))
        self._place_entry("mr_query", px0 + 28, field_y, px1 - px0 - 178, 38)
        entry = self.entries["mr_query"]
        entry.bind("<Return>", lambda _e: self._modrinth_search())
        search = CanvasButton(self, px1 - 138, field_y, px1 - 28, field_y + 38, "Искать",
                              command=self._modrinth_search, kind="primary", size=9,
                              enabled=not self._mr["busy"])
        self.modal_buttons.append(search)

        status_y = field_y + 54
        if self._mr["error"]:
            draw.text(cv, px0 + 28, status_y, str(self._mr["error"])[:92], 8, theme.DANGER,
                      "normal", "w", "modal")
        elif self._mr["status"]:
            draw.text(cv, px0 + 28, status_y, str(self._mr["status"])[:92], 8, theme.ACCENT,
                      "normal", "w", "modal")
        elif self._mr["busy"]:
            draw.text(cv, px0 + 28, status_y, "Ищу…", 8, theme.MUTED, "normal", "w", "modal")
        else:
            draw.text(cv, px0 + 28, status_y, "Введите запрос и нажмите «Искать».",
                      8, theme.DIM, "normal", "w", "modal")

        results = self._mr.get("results") or []
        per_page = 3
        pages = max(1, (len(results) + per_page - 1) // per_page)
        self._mr["page"] = min(int(self._mr.get("page") or 0), pages - 1)
        chunk = results[self._mr["page"] * per_page:(self._mr["page"] + 1) * per_page]
        y = field_y + 78
        if not chunk and not self._mr["busy"]:
            draw.rr(cv, px0 + 28, y, px1 - 28, y + 64, 12, fill=theme.CARD,
                    outline=theme.BORDER, tags="modal")
            draw.text(cv, px0 + 46, y + 26, "Ничего не найдено. Попробуйте другой запрос.",
                      9, theme.MUTED, "normal", "w", "modal")
        for item in chunk:
            draw.rr(cv, px0 + 28, y, px1 - 28, y + 68, 12, fill=theme.CARD,
                    outline=theme.BORDER, tags="modal")
            draw.text(cv, px0 + 46, y + 20, str(item.get("title") or "Mod")[:52], 10,
                      theme.TEXT, "bold", "w", "modal")
            description = str(item.get("description") or "")[:78]
            draw.text(cv, px0 + 46, y + 38, description, 8, theme.MUTED, "normal", "w", "modal")
            downloads = int(item.get("downloads") or 0)
            draw.text(cv, px0 + 46, y + 55, f"{downloads:,} загрузок".replace(",", " "),
                      7, theme.DIM, "normal", "w", "modal")
            installing = self._mr.get("installing") == item.get("id")
            install = CanvasButton(
                self, px1 - 144, y + 16, px1 - 40, y + 52,
                "Устанавливаю…" if installing else "Установить",
                command=lambda it=item: self._modrinth_install(it),
                kind="ghost" if installing else "primary", size=8,
                enabled=not self._mr["busy"],
            )
            self.modal_buttons.append(install)
            y += 74

        if pages > 1:
            draw.text(cv, (px0 + px1) / 2, py1 - 28,
                      f"стр. {self._mr['page'] + 1} / {pages}", 8, theme.MUTED,
                      "normal", "center", "modal")
            prev = CanvasButton(self, px1 - 214, py1 - 42, px1 - 168, py1 - 14, "←",
                                command=lambda: self._modrinth_go(-1), kind="ghost", size=9,
                                enabled=self._mr["page"] > 0 and not self._mr["busy"])
            nxt = CanvasButton(self, px1 - 160, py1 - 42, px1 - 114, py1 - 14, "→",
                               command=lambda: self._modrinth_go(1), kind="ghost", size=9,
                               enabled=self._mr["page"] < pages - 1 and not self._mr["busy"])
            self.modal_buttons.extend([prev, nxt])

    def _modrinth_search(self) -> None:
        if self._mr["busy"]:
            return
        entry = self.entries.get("mr_query")
        query = entry.get().strip() if entry is not None else str(self._mr.get("query") or "")
        self._mr.update(query=query, busy=True, error="", status="Ищу…", page=0)
        self._draw_modal()
        game, loader = self._instance_meta()

        def work() -> None:
            try:
                results = modrinth_core.search(query, game, loader)
                error = ""
            except modrinth_core.ModrinthError as exc:
                results = []
                error = str(exc)
            except Exception as exc:  # noqa: BLE001 - показываем пользователю
                results = []
                error = str(exc)
            self._mr.update(results=results, busy=False, error=error,
                            status=f"Найдено: {len(results)}" if not error else "")
            self._post(self._modrinth_redraw)

        threading.Thread(target=work, daemon=True, name="modrinth-search").start()

    def _modrinth_go(self, delta: int) -> None:
        self._mr["page"] = max(0, int(self._mr.get("page") or 0) + delta)
        self._draw_modal()

    def _modrinth_install(self, item: dict) -> None:
        if self._mr["busy"] or not self.installed_id:
            return
        project_id = str(item.get("id") or "")
        if not project_id:
            return
        self._mr.update(busy=True, installing=project_id, error="", status="Устанавливаю…")
        self._draw_modal()
        data_dir = self._data_dir()
        instance_id = self.installed_id
        game, loader = self._instance_meta()
        cancel = threading.Event()

        def work() -> None:
            try:
                plan = modrinth_core.resolve(project_id, game, loader)
                self._push_log_threadsafe(
                    f"Modrinth: {item.get('title')} + зависимостей: {max(0, len(plan) - 1)}"
                )
                result = modrinth_core.install(
                    plan,
                    mods_core.mods_dir(data_dir, instance_id),
                    log=self._push_log_threadsafe,
                    cancel=cancel,
                )
                status = f"Установлено файлов: {len(result['installed']) + len(result['skipped'])}"
                error = ""
            except modrinth_core.ModrinthError as exc:
                status = ""
                error = str(exc)
            except Exception as exc:  # noqa: BLE001 - показываем пользователю
                status = ""
                error = str(exc)
            self._mr.update(busy=False, installing="", status=status, error=error)
            self._post(self._modrinth_redraw)

        threading.Thread(target=work, daemon=True, name="modrinth-install").start()

    def _modrinth_redraw(self) -> None:
        if self.modal == "modrinth":
            self._draw_modal()
        elif self.page == "mods":
            self._show_page("mods")

    # ------------------------------------------------------------ модпаки

    def _import_modpack(self) -> None:
        if not self.installed_id:
            self._push_log("Сначала установите версию с загрузчиком.")
            return
        path = filedialog.askopenfilename(
            title="Выберите сборку",
            filetypes=[("Сборка Minecraft", "*.mrpack *.zip"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        data_dir = self._data_dir()
        instance_id = self.installed_id
        game, _loader = self._instance_meta()
        archive = Path(path)
        self._push_log(f"Сборка: {archive.name}")

        def work() -> None:
            try:
                plan = modpacks_core.inspect(archive)
                if plan["game_version"] and game and plan["game_version"] != game:
                    self._push_log_threadsafe(
                        f"Сборка для {plan['game_version']}, у вас {game} — проверьте совместимость"
                    )
                for warning in plan["warnings"]:
                    self._push_log_threadsafe(warning)
                result = modpacks_core.install(
                    data_dir, instance_id, archive, log_fn=self._push_log_threadsafe
                )
                self._push_log_threadsafe(
                    f"Сборка «{result['name']}»: файлов "
                    f"{result['installed'] + result['skipped']}, распаковано {result['extracted']}"
                )
            except modpacks_core.ModpackError as exc:
                self._push_log_threadsafe(f"Сборка не установилась: {exc}")
            except Exception as exc:  # noqa: BLE001 - показываем пользователю
                self._push_log_threadsafe(f"Сборка не установилась: {exc}")
            self._post(lambda: self._show_page("mods"))

        threading.Thread(target=work, daemon=True, name="modpack-install").start()

    # --------------------------------------------------------- обновления модов

    def _open_updates_modal(self) -> None:
        if not self.installed_id:
            self._push_log("Сначала установите версию с загрузчиком.")
            return
        _game, loader = self._instance_meta()
        if loader not in ("fabric", "forge", "neoforge"):
            self._push_log("Обновления доступны для Fabric, Forge или NeoForge")
            return
        self.modal = "updates"
        self._hide_entries()
        self._mu.update(checked=False, updates=[], error="", status="", applying="", stats={})
        self._draw_modal()
        self._mods_check_updates()

    def _mods_check_updates(self) -> None:
        if self._mu["busy"] or not self.installed_id:
            return
        self._mu.update(busy=True, error="", status="Проверяю…", checked=False)
        self._draw_modal()
        data_dir = self._data_dir()
        instance_id = self.installed_id
        game, loader = self._instance_meta()

        def work() -> None:
            try:
                result = modupdates_core.check(data_dir, instance_id, game, loader)
                updates = result["updates"]
                error = ""
                status = (f"Обновлений: {len(updates)} · проверено {result['checked']} · "
                          f"не с Modrinth: {result['unknown']}")
            except Exception as exc:  # noqa: BLE001 - показываем пользователю
                updates, error, status = [], str(exc), ""
            self._mu.update(busy=False, checked=True, updates=updates, error=error,
                            status=status)
            self._post(self._mods_redraw)

        threading.Thread(target=work, daemon=True, name="mods-updates").start()

    def _mods_apply(self, entry: dict | None) -> None:
        if self._mu["busy"] or not self.installed_id:
            return
        data_dir = self._data_dir()
        instance_id = self.installed_id
        items = [entry] if entry else list(self._mu["updates"])
        name = str((entry or {}).get("title") or "") if entry else ""
        self._mu.update(busy=True, applying=name, error="", status="Обновляю…")
        self._draw_modal()

        def work() -> None:
            done = 0
            failures = []
            for item in items:
                try:
                    modupdates_core.apply(data_dir, instance_id, item,
                                          log_fn=self._push_log_threadsafe)
                    done += 1
                except Exception as exc:  # noqa: BLE001 - показываем пользователю
                    failures.append(f"{item.get('title') or item.get('filename')}: {exc}")
            self._mu.update(
                busy=False,
                applying="",
                updates=[],
                status=f"Обновлено: {done}",
                error=failures[0] if failures else "",
                checked=bool(failures),
            )
            self._post(self._mods_redraw)

        threading.Thread(target=work, daemon=True, name="mods-apply").start()

    def _mods_redraw(self) -> None:
        if self.modal == "updates":
            self._draw_modal()
            return
        if self.page == "mods":
            self._show_page("mods")

    def _draw_updates(self, px0, px1, py0, py1) -> None:
        cv = self.canvas
        game, loader = self._instance_meta()
        draw.text(cv, px0 + 28, py0 + 34, "Обновления модов", 14, theme.TEXT, "bold", "w", "modal")
        draw.text(cv, px0 + 28, py0 + 56, f"{game} · {loader} · {self.installed_id or ''}",
                  8, theme.MUTED, "normal", "w", "modal")
        close = CanvasButton(self, px1 - 62, py0 + 20, px1 - 24, py0 + 52, "×",
                             command=self._close_modal, kind="ghost", size=12)
        self.modal_buttons.append(close)

        y = py0 + 84
        if self._mu["error"]:
            draw.text(cv, px0 + 28, y, str(self._mu["error"])[:88], 8, theme.DANGER,
                      "normal", "w", "modal")
        elif self._mu["status"]:
            draw.text(cv, px0 + 28, y, str(self._mu["status"])[:88], 8, theme.ACCENT,
                      "normal", "w", "modal")
        else:
            draw.text(cv, px0 + 28, y, "Сверяю установленные моды с Modrinth.",
                      8, theme.DIM, "normal", "w", "modal")

        rows = (self._mu.get("updates") or [])[:3]
        y += 30
        if self._mu["checked"] and not rows and not self._mu["busy"]:
            draw.rr(cv, px0 + 28, y, px1 - 28, y + 64, 12, fill=theme.CARD,
                    outline=theme.BORDER, tags="modal")
            draw.text(cv, px0 + 46, y + 26, "Обновлять нечего — все моды свежие.",
                      9, theme.MUTED, "normal", "w", "modal")
        for item in rows:
            draw.rr(cv, px0 + 24, y, px1 - 24, y + 72, 12, fill=theme.CARD,
                    outline=theme.BORDER, tags="modal")
            draw.text(cv, px0 + 40, y + 20, str(item.get("title") or item.get("filename"))[:44],
                      10, theme.TEXT, "bold", "w", "modal")
            draw.text(cv, px0 + 40, y + 40,
                      f"{item.get('current_version') or '?'} → {item.get('latest_version') or '?'}",
                      8, theme.MUTED, "normal", "w", "modal")
            draw.text(cv, px0 + 40, y + 56, str(item.get("filename"))[:52], 7, theme.DIM,
                      "normal", "w", "modal")
            button = CanvasButton(
                self, px1 - 138, y + 18, px1 - 42, y + 54, "Обновить",
                command=lambda it=item: self._mods_apply(it), kind="ghost", size=8,
                enabled=not self._mu["busy"],
            )
            self.modal_buttons.append(button)
            y += 78

        if len(self._mu.get("updates") or []) > 3:
            draw.text(cv, px0 + 28, py1 - 54,
                      f"И ещё {len(self._mu['updates']) - 3} — обновятся кнопкой ниже",
                      7, theme.DIM, "normal", "w", "modal")

        check = CanvasButton(self, px0 + 24, py1 - 56, px0 + 184, py1 - 20, "Проверить",
                             command=self._mods_check_updates, kind="ghost", size=9,
                             enabled=not self._mu["busy"])
        apply_all = CanvasButton(self, px1 - 214, py1 - 56, px1 - 24, py1 - 20,
                                 "Обновить всё", command=lambda: self._mods_apply(None),
                                 kind="primary", size=9,
                                 enabled=bool(self._mu["updates"]) and not self._mu["busy"])
        self.modal_buttons.extend([check, apply_all])

    # ------------------------------------------------------- ресурспаки и шейдеры

    def _open_packs_modal(self) -> None:
        if not self.installed_id:
            self._push_log("Сначала установите версию с загрузчиком.")
            return
        self.modal = "packs"
        self._hide_entries()
        self._packs.update(results=[], page=0, busy=False, error="", status="", installing="")
        self._draw_modal()
        self._packs_search()

    def _draw_packs(self, px0, px1, py0, py1) -> None:
        cv = self.canvas
        game, _loader = self._instance_meta()
        draw.text(cv, px0 + 28, py0 + 34, "Ресурспаки и шейдеры", 14, theme.TEXT, "bold",
                  "w", "modal")
        draw.text(cv, px0 + 28, py0 + 56, f"{game} · {self.installed_id or ''}", 8,
                  theme.MUTED, "normal", "w", "modal")
        close = CanvasButton(self, px1 - 62, py0 + 20, px1 - 24, py0 + 52, "×",
                             command=self._close_modal, kind="ghost", size=12)
        self.modal_buttons.append(close)

        tab_y = py0 + 74
        for index, (key, title) in enumerate((("resourcepack", "Ресурспаки"),
                                              ("shader", "Шейдеры"))):
            active = self._packs["kind"] == key
            left = px0 + 28 + index * 132
            button = CanvasButton(self, left, tab_y, left + 124, tab_y + 30, title,
                                  command=lambda k=key: self._packs_kind(k),
                                  kind="primary" if active else "ghost", size=8)
            self.modal_buttons.append(button)

        field_y = tab_y + 42
        draw.rr(cv, px0 + 28, field_y, px1 - 150, field_y + 36, 10, fill=theme.FIELD,
                outline=theme.BORDER, tags="modal")
        self.entries.get("pk_query") or self._entry("pk_query", str(self._packs.get("query") or ""))
        self._place_entry("pk_query", px0 + 28, field_y, px1 - px0 - 178, 36)
        entry = self.entries["pk_query"]
        entry.bind("<Return>", lambda _e: self._packs_search())
        search = CanvasButton(self, px1 - 138, field_y, px1 - 28, field_y + 36, "Искать",
                              command=self._packs_search, kind="primary", size=9,
                              enabled=not self._packs["busy"])
        self.modal_buttons.append(search)

        status_y = field_y + 52
        if self._packs["error"]:
            draw.text(cv, px0 + 28, status_y, str(self._packs["error"])[:88], 8,
                      theme.DANGER, "normal", "w", "modal")
        elif self._packs["status"]:
            draw.text(cv, px0 + 28, status_y, str(self._packs["status"])[:88], 8,
                      theme.ACCENT, "normal", "w", "modal")
        elif self._packs["busy"]:
            draw.text(cv, px0 + 28, status_y, "Ищу…", 8, theme.MUTED, "normal", "w", "modal")
        else:
            draw.text(cv, px0 + 28, status_y, "Введите запрос и нажмите «Искать».",
                      8, theme.DIM, "normal", "w", "modal")

        results = self._packs.get("results") or []
        per_page = 3
        pages = max(1, (len(results) + per_page - 1) // per_page)
        self._packs["page"] = min(int(self._packs.get("page") or 0), pages - 1)
        chunk = results[self._packs["page"] * per_page:(self._packs["page"] + 1) * per_page]
        y = field_y + 76
        for item in chunk:
            draw.rr(cv, px0 + 24, y, px1 - 24, y + 66, 12, fill=theme.CARD,
                    outline=theme.BORDER, tags="modal")
            draw.text(cv, px0 + 40, y + 20, str(item.get("title") or "Pack")[:46], 10,
                      theme.TEXT, "bold", "w", "modal")
            draw.text(cv, px0 + 40, y + 38, str(item.get("description") or "")[:74], 8,
                      theme.MUTED, "normal", "w", "modal")
            installing = self._packs.get("installing") == item.get("id")
            button = CanvasButton(
                self, px1 - 138, y + 16, px1 - 40, y + 52,
                "Ставлю…" if installing else "Установить",
                command=lambda it=item: self._packs_install(it),
                kind="ghost" if installing else "primary", size=8,
                enabled=not self._packs["busy"],
            )
            self.modal_buttons.append(button)
            y += 72

        if not chunk and not self._packs["busy"] and self._packs["status"]:
            draw.rr(cv, px0 + 24, y, px1 - 24, y + 56, 12, fill=theme.CARD,
                    outline=theme.BORDER, tags="modal")
            draw.text(cv, px0 + 40, y + 26, "Ничего не найдено.", 9, theme.MUTED,
                      "normal", "w", "modal")

        if pages > 1:
            draw.text(cv, (px0 + px1) / 2, py1 - 28, f"стр. {self._packs['page'] + 1} / {pages}",
                      8, theme.MUTED, "normal", "center", "modal")
            prev = CanvasButton(self, px1 - 214, py1 - 42, px1 - 168, py1 - 14, "←",
                                command=lambda: self._packs_go(-1), kind="ghost", size=9,
                                enabled=self._packs["page"] > 0 and not self._packs["busy"])
            nxt = CanvasButton(self, px1 - 160, py1 - 42, px1 - 114, py1 - 14, "→",
                               command=lambda: self._packs_go(1), kind="ghost", size=9,
                               enabled=self._packs["page"] < pages - 1 and not self._packs["busy"])
            self.modal_buttons.extend([prev, nxt])

    def _packs_kind(self, kind: str) -> None:
        if self._packs["kind"] == kind:
            return
        self._packs.update(kind=kind, results=[], page=0, status="", error="")
        self._draw_modal()
        self._packs_search()

    def _packs_go(self, delta: int) -> None:
        self._packs["page"] = max(0, int(self._packs.get("page") or 0) + delta)
        self._draw_modal()

    def _packs_search(self) -> None:
        if self._packs["busy"] or not self.installed_id:
            return
        entry = self.entries.get("pk_query")
        query = entry.get().strip() if entry is not None else str(self._packs.get("query") or "")
        self._packs.update(query=query, busy=True, error="", status="Ищу…", page=0)
        self._draw_modal()
        game, _loader = self._instance_meta()
        kind = self._packs["kind"]

        def work() -> None:
            try:
                results = modrinth_core.search_type(query, game, kind)
                error = ""
            except Exception as exc:  # noqa: BLE001 - показываем пользователю
                results, error = [], str(exc)
            self._packs.update(results=results, busy=False, error=error,
                               status=f"Найдено: {len(results)}" if not error else "")
            self._post(lambda: self._draw_modal() if self.modal == "packs" else None)

        threading.Thread(target=work, daemon=True, name="packs-search").start()

    def _packs_install(self, item: dict) -> None:
        if self._packs["busy"] or not self.installed_id:
            return
        project_id = str(item.get("id") or "")
        if not project_id:
            return
        self._packs.update(busy=True, installing=project_id, error="", status="Устанавливаю…")
        self._draw_modal()
        data_dir = self._data_dir()
        instance_id = self.installed_id
        game, _loader = self._instance_meta()
        kind = self._packs["kind"]

        def work() -> None:
            try:
                packs_core.install(data_dir, instance_id, kind, project_id, game,
                                   log_fn=self._push_log_threadsafe)
                status, error = "Установлено", ""
            except Exception as exc:  # noqa: BLE001 - показываем пользователю
                status, error = "", str(exc)
            self._packs.update(busy=False, installing="", status=status, error=error)
            self._post(lambda: self._draw_modal() if self.modal == "packs" else None)

        threading.Thread(target=work, daemon=True, name="pack-install").start()

    # ------------------------------------------------------------------ бэкапы

    def _open_backups_modal(self) -> None:
        if not self.installed_id:
            self._push_log("Сначала установите версию с загрузчиком.")
            return
        self.modal = "backups"
        self._hide_entries()
        self._bk.update(busy=False, error="", status="", restoring="", page=0)
        self._bk_load()
        self._draw_modal()

    def _bk_load(self) -> None:
        self._bk["items"] = backups_core.list_backups(self._data_dir(), self.installed_id)

    def _draw_backups(self, px0, px1, py0, py1) -> None:
        cv = self.canvas
        draw.text(cv, px0 + 28, py0 + 34, "Бэкапы миров", 14, theme.TEXT, "bold", "w", "modal")
        draw.text(cv, px0 + 28, py0 + 56,
                  f"{self.installed_id or ''} · {len(self._bk['items'])} архивов",
                  8, theme.MUTED, "normal", "w", "modal")
        close = CanvasButton(self, px1 - 62, py0 + 20, px1 - 24, py0 + 52, "×",
                             command=self._close_modal, kind="ghost", size=12)
        self.modal_buttons.append(close)

        make = CanvasButton(self, px0 + 24, py0 + 76, px0 + 204, py0 + 112,
                            "Создать бэкап", command=self._bk_create, kind="primary",
                            size=9, enabled=not self._bk["busy"])
        self.modal_buttons.append(make)

        status_y = py0 + 128
        if self._bk["error"]:
            draw.text(cv, px0 + 28, status_y, str(self._bk["error"])[:88], 8, theme.DANGER,
                      "normal", "w", "modal")
        elif self._bk["status"]:
            draw.text(cv, px0 + 28, status_y, str(self._bk["status"])[:88], 8, theme.ACCENT,
                      "normal", "w", "modal")
        else:
            draw.text(cv, px0 + 28, status_y,
                      "Бэкап — zip папки saves. Хранятся 5 последних.", 8, theme.DIM,
                      "normal", "w", "modal")

        items = self._bk["items"]
        per_page = 3
        pages = max(1, (len(items) + per_page - 1) // per_page)
        self._bk["page"] = min(int(self._bk.get("page") or 0), pages - 1)
        chunk = items[self._bk["page"] * per_page:(self._bk["page"] + 1) * per_page]
        y = py0 + 150
        if not items:
            draw.rr(cv, px0 + 24, y, px1 - 24, y + 56, 12, fill=theme.CARD,
                    outline=theme.BORDER, tags="modal")
            draw.text(cv, px0 + 40, y + 26, "Архивов пока нет.", 9, theme.MUTED,
                      "normal", "w", "modal")
        for item in chunk:
            draw.rr(cv, px0 + 24, y, px1 - 24, y + 66, 12, fill=theme.CARD,
                    outline=theme.BORDER, tags="modal")
            draw.text(cv, px0 + 40, y + 20, str(item.get("name"))[:40], 9, theme.TEXT,
                      "bold", "w", "modal")
            draw.text(cv, px0 + 40, y + 40,
                      f"{item.get('created')} · {paths.human_size(item.get('size') or 0)}", 8,
                      theme.MUTED, "normal", "w", "modal")
            restore = CanvasButton(
                self, px1 - 232, y + 16, px1 - 128, y + 50, "Восстановить",
                command=lambda n=item.get("name"): self._bk_restore(n), kind="ghost",
                size=8, enabled=not self._bk["busy"],
            )
            remove = CanvasButton(
                self, px1 - 118, y + 16, px1 - 34, y + 50, "Удалить",
                command=lambda n=item.get("name"): self._bk_delete(n), kind="ghost",
                size=8, enabled=not self._bk["busy"],
            )
            self.modal_buttons.extend([restore, remove])
            y += 72

        if pages > 1:
            draw.text(cv, (px0 + px1) / 2, py1 - 28, f"стр. {self._bk['page'] + 1} / {pages}",
                      8, theme.MUTED, "normal", "center", "modal")
            prev = CanvasButton(self, px1 - 214, py1 - 42, px1 - 168, py1 - 14, "←",
                                command=lambda: self._bk_go(-1), kind="ghost", size=9,
                                enabled=self._bk["page"] > 0)
            nxt = CanvasButton(self, px1 - 160, py1 - 42, px1 - 114, py1 - 14, "→",
                               command=lambda: self._bk_go(1), kind="ghost", size=9,
                               enabled=self._bk["page"] < pages - 1)
            self.modal_buttons.extend([prev, nxt])

    def _bk_go(self, delta: int) -> None:
        self._bk["page"] = max(0, int(self._bk.get("page") or 0) + delta)
        self._draw_modal()

    def _bk_create(self) -> None:
        if self._bk["busy"] or not self.installed_id:
            return
        self._bk.update(busy=True, error="", status="Создаю бэкап…")
        self._draw_modal()
        data_dir = self._data_dir()
        instance_id = self.installed_id

        def work() -> None:
            try:
                result = backups_core.create(data_dir, instance_id,
                                             log_fn=self._push_log_threadsafe)
                if result:
                    status, error = f"Готово: {result['name']}", ""
                else:
                    status, error = "", "Миров нет — сохранять нечего"
            except Exception as exc:  # noqa: BLE001 - показываем пользователю
                status, error = "", str(exc)
            self._bk.update(busy=False, status=status, error=error)
            self._post(lambda: self._bk_finish())

        threading.Thread(target=work, daemon=True, name="backup-create").start()

    def _bk_finish(self) -> None:
        self._bk_load()
        if self.modal == "backups":
            self._draw_modal()

    def _bk_restore(self, name) -> None:
        if self._bk["busy"] or not name:
            return
        if not messagebox.askyesno(
            i18n.tr("Бэкап"),
            i18n.tr(f"Восстановить {name}?\n\nТекущие миры сохранятся рядом "
                    "в папке saves-before-…"),
        ):
            return
        data_dir = self._data_dir()
        instance_id = self.installed_id
        self._bk.update(busy=True, error="", status="Восстанавливаю…")
        self._draw_modal()

        def work() -> None:
            try:
                backups_core.restore(data_dir, instance_id, name)
                status, error = "Миры восстановлены", ""
            except Exception as exc:  # noqa: BLE001 - показываем пользователю
                status, error = "", str(exc)
            self._bk.update(busy=False, status=status, error=error)
            self._post(self._bk_finish)

        threading.Thread(target=work, daemon=True, name="backup-restore").start()

    def _bk_delete(self, name) -> None:
        if self._bk["busy"] or not name:
            return
        if not messagebox.askyesno(i18n.tr("Бэкап"), i18n.tr(f"Удалить {name}?")):
            return
        if backups_core.delete(self._data_dir(), self.installed_id, name):
            self._bk.update(status="Бэкап удалён", error="")
        else:
            self._bk.update(error="Не удалось удалить бэкап", status="")
        self._bk_load()
        self._draw_modal()

    # -------------------------------------------------------------------- скин

    def _open_skin_modal(self) -> None:
        self.modal = "skin"
        self._hide_entries()
        nick = ""
        profile = profiles_core.active(self.cfg)
        if isinstance(profile, dict):
            nick = str(profile.get("nickname") or "")
        nick = nick or str(self.cfg.get("nickname") or "")
        self._skin = {"nick": nick, "busy": False, "error": "", "path": None, "image": None}
        self._draw_modal()
        if nick:
            self._skin_load(nick)

    def _draw_skin(self, px0, px1, py0, py1) -> None:
        cv = self.canvas
        draw.text(cv, px0 + 28, py0 + 34, "Скин", 14, theme.TEXT, "bold", "w", "modal")
        draw.text(cv, px0 + 28, py0 + 56, "Превью по нику и сохранение картинки",
                  8, theme.MUTED, "normal", "w", "modal")
        close = CanvasButton(self, px1 - 62, py0 + 20, px1 - 24, py0 + 52, "×",
                             command=self._close_modal, kind="ghost", size=12)
        self.modal_buttons.append(close)

        field_y = py0 + 76
        draw.rr(cv, px0 + 28, field_y, px1 - 150, field_y + 36, 10, fill=theme.FIELD,
                outline=theme.BORDER, tags="modal")
        self.entries.get("skin_nick") or self._entry("skin_nick", str(self._skin.get("nick") or ""))
        self._place_entry("skin_nick", px0 + 28, field_y, px1 - px0 - 178, 36)
        entry = self.entries["skin_nick"]
        entry.bind("<Return>", lambda _e: self._skin_search())
        show = CanvasButton(self, px1 - 138, field_y, px1 - 28, field_y + 36, "Показать",
                            command=self._skin_search, kind="primary", size=9,
                            enabled=not self._skin["busy"])
        self.modal_buttons.append(show)

        status_y = field_y + 52
        if self._skin["error"]:
            draw.text(cv, px0 + 28, status_y, str(self._skin["error"])[:88], 8,
                      theme.DANGER, "normal", "w", "modal")
        elif self._skin["busy"]:
            draw.text(cv, px0 + 28, status_y, "Загружаю…", 8, theme.MUTED, "normal",
                      "w", "modal")
        else:
            draw.text(cv, px0 + 28, status_y, "Ник — латиница, цифры и подчёркивание.",
                      8, theme.DIM, "normal", "w", "modal")

        image = self._skin.get("image")
        if image is not None:
            cv.create_image((px0 + px1) / 2, py0 + 260, image=image, tags=("modal", "skin_img"))
        else:
            draw.rr(cv, px0 + 28, py0 + 190, px1 - 28, py0 + 330, 14, fill=theme.CARD,
                    outline=theme.BORDER, tags="modal")
            draw.text(cv, (px0 + px1) / 2, py0 + 260, "Здесь появится голова скина",
                      9, theme.DIM, "normal", "center", "modal")

        save = CanvasButton(self, px0 + 24, py1 - 56, px0 + 204, py1 - 20,
                            "Сохранить PNG", command=self._skin_save, kind="ghost",
                            size=9, enabled=bool(self._skin.get("path")))
        folder = CanvasButton(self, px0 + 214, py1 - 56, px0 + 394, py1 - 20,
                              "Открыть папку", command=self._skin_folder, kind="ghost",
                              size=9)
        self.modal_buttons.extend([save, folder])

    def _skin_search(self) -> None:
        entry = self.entries.get("skin_nick")
        nick = entry.get().strip() if entry is not None else str(self._skin.get("nick") or "")
        self._skin_load(nick)

    def _skin_load(self, nick: str) -> None:
        if self._skin["busy"]:
            return
        self._skin.update(nick=nick, busy=True, error="")
        self._draw_modal()
        data_dir = self._data_dir()

        def work() -> None:
            try:
                path = skins_core.head(data_dir, nick, 192)
                image = None
                try:
                    image = tk.PhotoImage(file=str(path))
                except tk.TclError:
                    image = None
                error = "" if image is not None else "Картинка не открылась"
            except Exception as exc:  # noqa: BLE001 - показываем пользователю
                path, image, error = None, None, str(exc)
            self._skin.update(busy=False, path=path, image=image, error=error)
            self._post(lambda: self._draw_modal() if self.modal == "skin" else None)

        threading.Thread(target=work, daemon=True, name="skin-load").start()

    def _skin_save(self) -> None:
        source = self._skin.get("path")
        if not source:
            return
        nick = str(self._skin.get("nick") or "skin")
        target = filedialog.asksaveasfilename(
            title="Сохранить скин",
            defaultextension=".png",
            initialfile=f"{nick}-skin.png",
            filetypes=[("PNG", "*.png")],
        )
        if not target:
            return
        try:
            skins_core.save_copy(Path(source), Path(target))
            self._push_log(f"Скин сохранён: {target}")
        except OSError as exc:
            self._push_log(f"Скин не сохранился: {exc}")

    def _skin_folder(self) -> None:
        target = Path(self._data_dir()) / "cache" / "skins"
        target.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(target))  # noqa: S606 - открытие проводника
        except OSError:
            self._push_log("Проводник не открылся")

    # ------------------------------------------------------------ библиотека

    def _draw_library(self):
        cv = self.canvas
        self._header("Библиотека", "Все установленные версии · запуск в один клик")
        data_dir = self._data_dir()
        items = loaders.installed_versions(data_dir)
        per_page = 5
        pages = max(1, (len(items) + per_page - 1) // per_page)
        self.library_offset = min(self.library_offset, pages - 1)
        chunk = items[self.library_offset * per_page:(self.library_offset + 1) * per_page]

        if not items:
            draw.rr(cv, CX0, 140, CX1, 250, 16, fill=theme.CARD_BOTTOM, outline=theme.BORDER, tags="page")
            draw.text(cv, CX0 + 28, 176, "Пока ничего не установлено", 12, theme.TEXT, "bold", "w", "page")
            draw.text(cv, CX0 + 28, 202, "Выберите версию на странице «Играть» и нажмите «Скачать».",
                      9, theme.MUTED, "normal", "w", "page")

        y = 140
        for item in chunk:
            vid = item["id"]
            accent = theme.loader_accent(item["loader"])
            launching = self.launching_id == vid
            running = self.running_id == vid
            selected = self.installed_id == vid

            draw.rr(cv, CX0, y, CX1, y + 92, 16,
                    fill=theme.SELECT_BG if selected else theme.CARD,
                    outline=accent if selected else theme.BORDER, width=1 if selected else 2,
                    tags="page")
            draw.cube(cv, CX0 + 44, y + 46, 34, accent, tags="page")
            draw.text(cv, CX0 + 80, y + 26, f"Minecraft {item['game']}", 13, theme.TEXT, "bold", "w", "page")
            draw.text(cv, CX0 + 80, y + 47, f"{item['label']} · {item['id']}", 9, theme.MUTED,
                      "normal", "w", "page")

            status_color = theme.ACCENT if (running or selected) else theme.MUTED
            status_text = ("запущена" if running else
                           "запускаю…" if launching else
                           f"установлена · {paths.human_size(item['size'])}")
            draw.dot(cv, CX0 + 84, y + 68, 3, status_color, tags="page")
            draw.text(cv, CX0 + 94, y + 68, status_text, 8, status_color, "normal", "w", "page")

            instance_path = instances_core.path(data_dir, vid)
            path_text = str(instance_path if instance_path.is_dir() else item["path"])
            if len(path_text) > 56:
                path_text = "…" + path_text[-55:]
            draw.text(cv, CX0 + 320, y + 68, path_text, 8, theme.DIM, "normal", "w", "page")

            if running or launching:
                play_btn = CanvasButton(self, CX1 - 352, y + 26, CX1 - 250, y + 66, "Запущена",
                                        command=None, kind="ghost", size=9, enabled=False)
            else:
                play_btn = CanvasButton(self, CX1 - 352, y + 26, CX1 - 250, y + 66, "Играть",
                                        command=lambda it=item: self._start_game(it["id"]),
                                        kind="primary", size=9)
            open_btn = CanvasButton(self, CX1 - 238, y + 26, CX1 - 132, y + 66, "Открыть",
                                    command=lambda v=vid: self._open_version_folder(v),
                                    kind="ghost", size=9)
            label = "Точно?" if self._confirm_delete == vid else "Удалить"
            del_btn = CanvasButton(self, CX1 - 120, y + 26, CX1 - 24, y + 66, label,
                                   command=lambda v=vid: self._ask_delete(v), kind="danger", size=9)
            self.page_buttons.extend([play_btn, open_btn, del_btn])
            y += 100

        draw.text(cv, CX0, y + 14, f"Папка сборок: {data_dir / 'instances'}"[:110], 8, theme.DIM, "normal", "w", "page")
        if pages > 1:
            draw.text(cv, CX1 - 90, y + 14, f"стр. {self.library_offset + 1} / {pages}", 8,
                      theme.MUTED, "normal", "e", "page")
            prev = CanvasButton(self, CX1 - 84, y - 4, CX1 - 54, y + 26, "←",
                                command=lambda: self._library_go(-1), kind="ghost", size=9,
                                enabled=self.library_offset > 0)
            nxt = CanvasButton(self, CX1 - 50, y - 4, CX1 - 20, y + 26, "→",
                               command=lambda: self._library_go(1), kind="ghost", size=9,
                               enabled=self.library_offset < pages - 1)
            self.page_buttons.extend([prev, nxt])

    def _library_go(self, delta):
        self.library_offset = max(0, self.library_offset + delta)
        self._show_page("library")

    def _on_wheel(self, event):
        step = -1 if event.delta > 0 else 1
        if self.page == "library":
            self._library_go(step)
        elif self.page == "mods":
            self._mods_go(step)
        elif self.page == "play" and event.x < LEFT_X1 and event.y > GRID_TOP - 20:
            self._grid_go(step)

    def _on_focus(self, _event=None):
        """Вернулись в окно — проверяем, не появились ли новые моды в папке."""
        if self.page != "mods" or self.modal:
            return
        items = mods_core.list_mods(self._data_dir(), self.installed_id)
        if mods_core.signature(items) != self._mods_signature:
            self._mods_signature = mods_core.signature(items)
            self._show_page("mods")

    def _open_folder(self, _event=None):
        target = self._data_dir()
        target.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(target))  # noqa: S606 - открытие проводника
        except OSError:
            self._push_log("Проводник не открылся")

    def _open_version_folder(self, vid: str):
        target = instances_core.path(self._data_dir(), vid)
        if not target.is_dir():
            target = self._data_dir() / "versions" / vid
        if not target.is_dir():
            target = self._data_dir()
        try:
            os.startfile(str(target))  # noqa: S606
        except OSError:
            self._push_log("Проводник не открылся")

    def _open_logs(self):
        target = self._data_dir() / "logs"
        target.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(target))  # noqa: S606
        except OSError:
            self._push_log("Проводник не открылся")

    def _ask_delete(self, vid):
        if self._confirm_delete != vid:
            self._confirm_delete = vid
            self._show_page("library")
            return
        self._confirm_delete = None
        uninstall(self._data_dir(), vid)
        self._size_cache.pop(vid, None)
        self._scan_installed()
        self._show_page("library")
        self._push_log(f"Удалена версия {vid}")

    # -------------------------------------------------------------- настройки

    def _draw_settings(self):
        cv = self.canvas
        self._header("Настройки", "Никнейм, память, Java и папка игры")
        draw.rr(cv, CX0, 132, CX1, 680, 18, fill=theme.PANEL, outline=theme.PANEL_BORDER, tags="page")

        draw.text(cv, CX0 + 28, 160, "Никнейм", 9, theme.MUTED, "normal", "w", "page")
        draw.rr(cv, CX0 + 28, 178, CX0 + 348, 216, 10, fill=theme.FIELD, outline=theme.BORDER, tags="page")
        self.entries.get("nick_settings") or self._entry("nick_settings", self.cfg.get("nickname") or "Steve")
        self._place_entry("nick_settings", CX0 + 28, 178, 320, 38)
        draw.text(cv, CX0 + 360, 197, "до 16 символов, для одиночной игры", 8, theme.DIM, "normal", "w", "page")
        skin = CanvasButton(self, CX0 + 700, 178, CX0 + 844, 216, "Скин",
                            command=self._open_skin_modal, kind="ghost", size=9)
        self.page_buttons.append(skin)

        draw.text(cv, CX0 + 28, 244, "Оперативная память", 9, theme.MUTED, "normal", "w", "page")
        self._draw_ram_slider(CX0 + 28, 276)

        draw.text(cv, CX0 + 28, 316, "Java", 9, theme.MUTED, "normal", "w", "page")
        draw.rr(cv, CX0 + 28, 334, CX0 + 480, 372, 10, fill=theme.FIELD, outline=theme.BORDER, tags="page")
        self.entries.get("java") or self._entry("java", self.cfg.get("java_path") or "")
        self._place_entry("java", CX0 + 28, 334, 452, 38)
        pick = CanvasButton(self, CX0 + 488, 332, CX0 + 604, 374, "Обзор",
                            command=self._pick_java, kind="ghost", size=9)
        auto = CanvasButton(self, CX0 + 612, 332, CX0 + 728, 374, "Авто",
                            command=self._auto_java, kind="ghost", size=9)
        download = CanvasButton(self, CX0 + 736, 332, CX0 + 844, 374,
                                "Скачать Java" if not self.java_ok else "Проверить",
                                command=self._install_java, kind="ghost", size=9,
                                enabled=not self.java_busy)
        self.btn_java = download
        self.page_buttons.extend([pick, auto, download])
        self.settings_java_id = draw.text(cv, CX0 + 28, 386, self.java_text, 8,
                                          theme.ACCENT if self.java_ok else theme.DANGER,
                                          "normal", "w", "page")
        draw.text(cv, CX0 + 400, 386, "нужную JRE лаунчер может скачать сам — кнопка справа",
                  8, theme.DIM, "normal", "w", "page")

        draw.text(cv, CX0 + 28, 414, "Аргументы JVM", 9, theme.MUTED, "normal", "w", "page")
        draw.rr(cv, CX0 + 28, 432, CX0 + 844, 470, 10, fill=theme.FIELD, outline=theme.BORDER, tags="page")
        self.entries.get("jvm_args") or self._entry("jvm_args", str(self.cfg.get("jvm_args") or ""))
        self._place_entry("jvm_args", CX0 + 28, 432, 816, 38)
        draw.text(cv, CX0 + 28, 478,
                  "Дополнительные аргументы Java, например -XX:+UseG1GC", 8,
                  theme.DIM, "normal", "w", "page")

        draw.text(cv, CX0 + 28, 500, "Папка игры", 9, theme.MUTED, "normal", "w", "page")
        draw.rr(cv, CX0 + 28, 518, CX0 + 480, 556, 10, fill=theme.FIELD, outline=theme.BORDER, tags="page")
        self.entries.get("data_dir") or self._entry("data_dir", self.cfg.get("data_dir") or "")
        self._place_entry("data_dir", CX0 + 28, 518, 452, 38)
        open_btn = CanvasButton(self, CX0 + 488, 516, CX0 + 604, 558, "Открыть",
                                command=self._open_folder, kind="ghost", size=9)
        logs_btn = CanvasButton(self, CX0 + 612, 516, CX0 + 728, 558, "Логи",
                                command=self._open_logs, kind="ghost", size=9)
        self.page_buttons.extend([open_btn, logs_btn])

        save = CanvasButton(self, CX0 + 28, 584, CX0 + 208, 630, "Сохранить",
                            command=self._save_settings, kind="primary", size=10)
        self.page_buttons.append(save)
        self.settings_hint = draw.text(cv, CX0 + 224, 607,
                                       "Java можно не указывать — найдётся сама.", 8,
                                       theme.DIM, "normal", "w", "page")

        draw.text(cv, CX0 + 512, 594, "Тема", 9, theme.MUTED, "normal", "w", "page")
        theme_x = CX0 + 572
        for key, title in theme.palettes():
            active = theme.CURRENT == key
            button = CanvasButton(self, theme_x, 576, theme_x + 83, 612, title,
                                  command=lambda k=key: self._set_theme(k),
                                  kind="primary" if active else "ghost", size=8)
            self.page_buttons.append(button)
            theme_x += 90

        draw.text(cv, CX0 + 512, 656, "Язык", 9, theme.MUTED, "normal", "w", "page")
        language_x = CX0 + 572
        for key, title in i18n.LANGUAGES.items():
            active = i18n.CURRENT == key
            button = CanvasButton(self, language_x, 638, language_x + 80, 674, title,
                                  command=lambda k=key: self._set_language(k),
                                  kind="primary" if active else "ghost", size=8)
            self.page_buttons.append(button)
            language_x += 87

    def _draw_ram_slider(self, x, y):
        cv = self.canvas
        self.ram_values = [1024, 1536, 2048, 3072, 4096, 6144, 8192, 12288]
        try:
            self.ram_index = self.ram_values.index(int(self.cfg.get("ram_mb", 2048)))
        except ValueError:
            self.ram_index = 2
        self.ram_x0, self.ram_x1, self.ram_y = x, x + 320, y
        draw.rr(cv, x, y - 3, x + 320, y + 3, 3, fill=theme.TRACK, outline="", tags="page")
        self.ram_fill = draw.rr(cv, x, y - 3, x + 40, y + 3, 3, fill=theme.ACCENT, outline="", tags="page")
        self.ram_knob = draw.dot(cv, x, y, 8, theme.ACCENT, tags=("page", "ram_knob"))
        self.ram_text = draw.text(cv, x + 340, y, "", 9, theme.TEXT, "bold", "w", "page")
        cv.tag_bind("ram_knob", "<B1-Motion>", self._ram_drag)
        cv.tag_bind("ram_knob", "<Button-1>", self._ram_drag)
        cv.tag_bind("ram_knob", "<Enter>", lambda e: cv.configure(cursor="hand2"))
        cv.tag_bind("ram_knob", "<Leave>", lambda e: cv.configure(cursor=""))
        self._ram_paint()

    def _ram_drag(self, event):
        ratio = (event.x - self.ram_x0) / max(1, self.ram_x1 - self.ram_x0)
        self.ram_index = max(0, min(len(self.ram_values) - 1, round(ratio * (len(self.ram_values) - 1))))
        self._ram_paint()

    def _ram_paint(self):
        cv = self.canvas
        count = len(self.ram_values)
        ratio = self.ram_index / (count - 1)
        knob_x = self.ram_x0 + (self.ram_x1 - self.ram_x0) * ratio
        cv.coords(self.ram_knob, knob_x - 8, self.ram_y - 8, knob_x + 8, self.ram_y + 8)
        cv.coords(self.ram_fill, *self._round_points(self.ram_x0, self.ram_y - 3,
                                                     max(self.ram_x0 + 6, knob_x), self.ram_y + 3, 3))
        value = self.ram_values[self.ram_index]
        cv.itemconfigure(self.ram_text, text=i18n.tr(f"{value} МБ"))

    def _round_points(self, x0, y0, x1, y1, r):
        import math
        points = []
        for cx, cy, start, end in ((x0 + r, y0 + r, 180, 270), (x1 - r, y0 + r, 270, 360),
                                   (x1 - r, y1 - r, 0, 90), (x0 + r, y1 - r, 90, 180)):
            for i in range(7):
                angle = math.radians(start + (end - start) * i / 6)
                points.extend((cx + r * math.cos(angle), cy + r * math.sin(angle)))
        return points

    def _pick_java(self):
        chosen = filedialog.askopenfilename(
            title=i18n.tr("Выбери javaw.exe"),
            filetypes=[("Java", "javaw.exe java.exe"), (i18n.tr("Все файлы"), "*.*")],
        )
        if chosen:
            entry = self.entries.get("java")
            if entry is not None:
                entry.delete(0, "end")
                entry.insert(0, chosen)

    def _auto_java(self):
        entry = self.entries.get("java")
        if entry is not None:
            entry.delete(0, "end")
        self.cfg["java_path"] = ""
        java_core.clear_cache()
        self._detect_java()

    def _install_java(self):
        if self.java_busy:
            return
        if self.java_ok:
            self._detect_java()
            return
        needed = self.java_required
        self.java_busy = True
        if hasattr(self, "btn_java"):
            self.btn_java.set_enabled(False)
            self.btn_java.set_label("Скачиваю…")
        self._push_log(f"Скачиваю официальную JRE {needed} с Adoptium…")

        def run():
            try:
                path, major = java_core.ensure_java(needed, log=self._push_log_threadsafe)
                self.java_text = f"Java {major} найдена"
                self.java_ok = True
                self._push_log_threadsafe(f"Java {major} готова: {path}")
            except Exception as exc:  # noqa: BLE001
                self.java_text = f"Нужна Java {needed}"
                self.java_ok = False
                self._push_log_threadsafe(f"Java не скачалась: {exc}")
            self.java_busy = False
            self._post(self._refresh_java_chip)
            self._post(self._rebuild_current_page)

        threading.Thread(target=run, daemon=True).start()

    def _rebuild_current_page(self):
        self._show_page(self.page)

    def _save_settings(self):
        entry = self.entries.get("nick_settings")
        self.cfg["nickname"] = ((entry.get() if entry else "") or "Steve").strip()[:16]
        self.cfg["ram_mb"] = self.ram_values[self.ram_index]
        java_entry = self.entries.get("java")
        self.cfg["java_path"] = (java_entry.get().strip() if java_entry else "")
        jvm_entry = self.entries.get("jvm_args")
        self.cfg["jvm_args"] = (jvm_entry.get().strip() if jvm_entry else "")
        data_entry = self.entries.get("data_dir")
        new_dir = data_entry.get().strip() if data_entry else ""
        if new_dir:
            self.cfg["data_dir"] = new_dir
        paths.save_config(self.cfg)
        self._size_cache.clear()
        self._scan_installed()
        java_core.clear_cache()
        self._detect_java()
        if hasattr(self, "settings_hint"):
            self.canvas.itemconfigure(self.settings_hint,
                                      text="Сохранено. Никнейм и память применятся при запуске.",
                                      fill=theme.ACCENT)

    # ------------------------------------------------------------------- о нас

    def _draw_about(self):
        cv = self.canvas
        self._header("О лаунчере", "Чистый лаунчер: ничего не предустановлено")
        draw.rr(cv, CX0, 132, CX1, 648, 18, fill=theme.PANEL, outline=theme.PANEL_BORDER, tags="page")
        draw.cube(cv, CX0 + 76, 196, 64, theme.ACCENT, tags="page")
        draw.text(cv, CX0 + 124, 176, f"Vleyz Launcher {APP_VERSION}", 16, theme.TEXT, "bold", "w", "page")
        draw.text(cv, CX0 + 124, 200, "Сделал vleyz", 13, theme.ACCENT, "bold", "w", "page")
        draw.text(cv, CX0 + 124, 222, "Любая версия Minecraft, любой загрузчик — в пару кликов.",
                  9, theme.MUTED, "normal", "w", "page")

        points = [
            ("Все версии из манифеста", "Список релизов Mojang подтягивается онлайн: поиск по id, страницы."),
            ("Fabric, Forge, NeoForge", "Fabric ставится профилем, Forge и NeoForge — их официальными установщиками."),
            ("Проверка целостности", "Каждый файл сверяется по SHA1, битые и подменённые загрузки отбрасываются."),
            ("Java сама", "Если нужной Java нет, лаунчер скачает официальную сборку Adoptium."),
            ("Своя папка игры", "Профиль живёт отдельно от .minecraft, ваши миры и настройки не затрагиваются."),
        ]
        y = 272
        for title, body in points:
            draw.dot(cv, CX0 + 36, y + 8, 3, theme.ACCENT, tags="page")
            draw.text(cv, CX0 + 50, y, title, 10, theme.TEXT, "bold", "w", "page")
            draw.text(cv, CX0 + 50, y + 18, body, 9, theme.MUTED, "normal", "w", "page")
            y += 46

        draw.hline(cv, CX0 + 28, CX1 - 28, 520, theme.DIVIDER, tags="page")
        draw.text(cv, CX0 + 28, 540, "Minecraft — торговая марка Mojang Studios. Лаунчер не связан с Mojang.",
                  8, theme.DIM, "normal", "w", "page")
        draw.text(cv, CX0 + 28, 560, "Играть можно только на аккаунте, который принадлежит вам (EULA Mojang).",
                  8, theme.DIM, "normal", "w", "page")
        check = CanvasButton(self, CX0 + 28, 580, CX0 + 268, 620, "Проверить обновления",
                             command=lambda: self._check_updates(manual=True), kind="ghost", size=9)
        self.page_buttons.append(check)
        draw.text(cv, CX1 - 28, 600, "Сделал vleyz", 9, theme.DIM, "normal", "e", "page")

    # ------------------------------------------------------------------ список

    def _load_releases(self):
        if self.releases_loading:
            return
        self.releases_loading = True
        self.releases_error = ""
        if self.page == "play":
            self._show_page("play")

        def run():
            try:
                data = version_core.releases(self._data_dir())
                error = ""
            except Exception as exc:  # noqa: BLE001 - без сети списка нет
                data = []
                error = f"нет связи с Mojang: {exc}"[:70]
            self.releases = data
            self.releases_loading = False
            self.releases_error = error
            self._post(self._after_releases)

        threading.Thread(target=run, daemon=True).start()

    def _after_releases(self):
        ids = {str(version.get("id")) for version in (self.releases or [])}
        if self.releases and (not self.game or self.game not in ids):
            self.game = str(self.releases[0].get("id"))
            self.cfg["selected_version"] = self.game
            paths.save_config(self.cfg)
            self._scan_installed()
            self._resolve_choices()
            self._detect_java()
        if self.page == "play":
            self._show_page("play")
        else:
            self._refresh_nav()

    # --------------------------------------------------------------- установка

    def _primary_action(self):
        if self.worker and self.worker.is_alive():
            self.cancel_event.set()
            self._push_log("Останавливаю загрузку…")
            return
        self._start_install()

    def _repair_selected(self) -> None:
        """Проверить и докачать файлы выбранной версии на странице «Версии»."""
        if not self.installed_id:
            return
        if self.worker and self.worker.is_alive():
            self._push_log("Дождись окончания текущей операции.")
            return
        self._show_page("play")
        self._start_install(repair=True)

    def _start_install(self, repair: bool = False):
        if self.worker and self.worker.is_alive():
            return
        if not self.game:
            self._push_log("Сначала выбери версию.")
            return
        if self.plan_error or self._choice(self.loader).get("state") == "error":
            message = self.plan_error or str(self._choice(self.loader).get("message") or "загрузчик недоступен")
            self._push_log(f"Нечего качать: {message}")
            return
        self.cancel_event = threading.Event()
        self.progress = Progress()
        self._display_pct = 0.0
        self._install_repair = repair
        self.btn_primary.set_label("Отмена")
        self.btn_primary.set_kind("danger")
        self.btn_play.set_enabled(False)
        self.canvas.itemconfigure(
            self.stage_id,
            text=(f"Проверяю Minecraft {self.game}" if repair else f"Скачиваю Minecraft {self.game}"),
        )
        self._push_log("Проверяю файлы…" if repair else "Подключаюсь к официальным серверам…")

        game = self.game
        loader = self.loader
        data_dir = self._data_dir()

        def run():
            installer = Installer(data_dir, self.progress, log=self._push_log_threadsafe, cancel=self.cancel_event)
            try:
                result = installer.install(game, loader, repair=repair)
                self._after_install(result.get("version_id") or game, None)
            except DownloadCancelled:
                self._after_install(game, "cancelled")
            except (InstallError, loaders.LoaderError) as exc:
                self._after_install(game, str(exc))
            except Exception as exc:  # noqa: BLE001 - показываем пользователю
                self._after_install(game, str(exc))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _after_install(self, version_id, error):
        self._post(lambda: self._finish_ui(version_id, error))

    def _finish_ui(self, version_id, error):
        self._size_cache.clear()
        self._scan_installed()
        try:
            self._show_page("play")
        except tk.TclError:
            return
        if error == "cancelled":
            self.canvas.itemconfigure(self.stage_id, text="Загрузка остановлена")
            self._push_log("Загрузка остановлена — можно продолжить в любой момент.")
        elif error:
            self.canvas.itemconfigure(self.stage_id, text="Ошибка установки")
            self._push_log(f"Ошибка: {error}")
        elif self.installed_id:
            try:
                instances_core.ensure(self._data_dir(), self.installed_id)
            except OSError:
                pass
            self.canvas.itemconfigure(self.stage_id, text="Готово к запуску")
            if self._install_repair:
                self._push_log("Файлы проверены — можно запускать.")
            else:
                self._push_log(f"{version_id} установлена. Нажми «Играть».")
            self._display_pct = 100.0
            self.canvas.itemconfigure(self.pct_id, text="100 %")
            self._paint_bar(100.0)
        self._install_repair = False
        if not self.installed_id:
            self._display_pct = 0.0
            self._paint_bar(0.0)

    def _push_log(self, message):
        stamp = time.strftime("%H:%M:%S")
        self.log_lines.append(f"{stamp}  {message}")
        self.log_lines = self.log_lines[-40:]
        if hasattr(self, "stage_id") and self.page == "play":
            try:
                self.canvas.itemconfigure(self.log_id, text=self.log_lines[-1][:130])
            except (tk.TclError, AttributeError):
                pass

    def _push_log_threadsafe(self, message):
        self._post(lambda: self._push_log(message))

    # ------------------------------------------------------------------ запуск

    def _set_stage(self, text):
        """Безопасно обновляет строку статуса (даже если она не нарисована сейчас)."""
        self._post(lambda: self._set_stage_now(text))

    def _set_stage_now(self, text):
        self.status_text = str(text)[:46]
        if not hasattr(self, "stage_id"):
            return
        try:
            self.canvas.itemconfigure(self.stage_id, text=self.status_text)
        except tk.TclError:
            pass

    def _start_game(self, version_id=None, server_address=None):
        if self.worker and self.worker.is_alive():
            self._push_log("Дождись окончания загрузки.")
            return
        if self.launch_worker and self.launch_worker.is_alive():
            return
        version_id = version_id or self.installed_id
        if not version_id:
            self._push_log("Сначала скачай версию.")
            return
        try:
            needed = version_core.java_requirement(self._data_dir(), version_id)
        except Exception:  # noqa: BLE001 - нет json на диске
            needed = 8
        self.java_required = needed
        found = java_core.find_java(needed, self.cfg.get("java_path", ""))
        if not found:
            self._push_log(f"{version_id}: нужна Java {needed} — кнопка «Скачать Java» в настройках.")
            return
        nickname = (self.cfg.get("nickname") or "Steve").strip()[:16] or "Steve"
        data_dir = self._data_dir()

        # запущенная из библиотеки версия становится выбранной
        row = next((entry for entry in loaders.installed_versions(data_dir)
                    if entry["id"] == version_id), None)
        if row is not None:
            self.game = row["game"]
            self.loader = row["loader"] if row["loader"] in loaders.LOADER_BY_ID else "vanilla"
            self.installed_id = row["id"]
            self.installed_size = row["size"]
            self.cfg["selected_version"] = self.game
            self.cfg["selected_loader"] = self.loader
            paths.save_config(self.cfg)
            self.launching_id = version_id
            self.running_id = None
            if self.page == "library":
                self._show_page("library")

        self._push_log(f"Запускаю {version_id} на Java {found[1]}…")
        if server_address:
            self._push_log(f"Автоподключение к серверу {server_address}")
        self._set_stage("Запускаю игру")
        game_dir = instances_core.ensure(data_dir, version_id)
        servers_core.sync_servers_dat(game_dir, self.cfg)
        ram = int(self.cfg.get("ram_mb", 2048))
        jvm_args = str(self.cfg.get("jvm_args") or "")

        def watch(proc, started):
            code = proc.wait()
            self.launching_id = None
            self.running_id = None
            summary = ""
            if code not in (0, None):
                report = launch_core.latest_crash_report(game_dir, started)
                summary = launch_core.crash_summary(report) if report else ""
            if summary:
                self._push_log_threadsafe(f"Краш: {summary}")
                self._set_stage("Игра упала")
            else:
                self._set_stage("Готов к работе")
            self._post(self._refresh_launch_state)

        def run():
            started = time.time()
            server = None
            if server_address:
                server = servers_core.resolve_address(server_address)
            session = None
            active_nick = nickname
            mode = self._account_mode()
            try:
                if mode == "microsoft":
                    session = microsoft_core.ensure_session(self.cfg)
                    paths.save_config(self.cfg)
                    active_nick = str(session.get("profile_name") or active_nick)
                    self._push_log_threadsafe(f"Вход Microsoft: {active_nick}")
                elif mode == "elyby":
                    session = elyby_core.launch_session(
                        self.cfg, data_dir, log=self._push_log_threadsafe,
                        cancel=self.cancel_event,
                    )
                    paths.save_config(self.cfg)
                    active_nick = str(session.get("profile_name") or active_nick)
                    self._push_log_threadsafe(f"Вход Ely.by: {active_nick}")
            except (microsoft_core.AuthError, elyby_core.AuthError) as exc:
                self.launching_id = None
                self._push_log_threadsafe(f"Вход не выполнен: {exc}")
                self._set_stage("Ошибка входа")
                self._post(self._refresh_launch_state)
                return
            try:
                process, log_path = launch_core.launch(
                    data_dir, version_id, active_nick, ram,
                    found[0], server, session, jvm_args, game_dir,
                )
            except launch_core.LaunchError as exc:
                self.launching_id = None
                self._push_log_threadsafe(str(exc))
                self._set_stage("Ошибка запуска")
                self._post(self._refresh_launch_state)
                return
            time.sleep(6)
            code = process.poll()
            if code is None:
                self.launching_id = None
                self.running_id = version_id
                self._set_stage("Игра запущена")
                self._push_log_threadsafe("Игра запущена. Хорошей игры!")
                self._post(self._refresh_launch_state)
                threading.Thread(target=watch, args=(process, started), daemon=True).start()
                return
            report = launch_core.latest_crash_report(game_dir, started)
            summary = launch_core.crash_summary(report) if report else ""
            self.launching_id = None
            self.running_id = None
            self._set_stage("Игра упала" if summary else "Игра закрылась")
            if summary:
                self._push_log_threadsafe(f"Краш: {summary}")
                self._push_log_threadsafe(f"Отчёт: {report.name}")
            else:
                tail = launch_core.read_log_tail(log_path, 3)
                reason = next((line for line in reversed(tail) if "Exception" in line or "Error" in line), "")
                self._push_log_threadsafe(f"Игра завершилась (код {code}). {reason[:110]}")
            if code != 0:
                self._push_log_threadsafe(f"Лог: {log_path}")
            self._post(self._refresh_launch_state)

        self.launch_worker = threading.Thread(target=run, daemon=True)
        self.launch_worker.start()

    def _refresh_launch_state(self):
        if self.page in ("library", "play"):
            self._show_page(self.page)

    def _detect_java(self):
        """Проверяет, хватает ли установленной Java для выбранной версии."""
        self.java_text = "Java: проверяю…"
        self.java_ok = False
        game = self.game
        data_dir = self._data_dir()
        if not game:
            self.java_text = "Java: —"
            return

        def run():
            try:
                needed = version_core.java_requirement(data_dir, game)
            except Exception:  # noqa: BLE001 - нет описания версии
                needed = 8
            try:
                info = java_core.find_java(needed, self.cfg.get("java_path", ""))
            except Exception:  # noqa: BLE001
                info = None
            self.java_required = needed
            if info:
                self.java_text = f"Java {info[1]}"
                self.java_ok = True
            else:
                self.java_text = f"нужна Java {needed}"
                self.java_ok = False
            self._post(self._refresh_java_chip)

        threading.Thread(target=run, daemon=True).start()

    def _refresh_java_chip(self):
        if self.page == "play":
            try:
                self._show_page("play")
            except tk.TclError:
                pass
        elif self.page == "settings" and hasattr(self, "settings_java_id"):
            try:
                self.canvas.itemconfigure(self.settings_java_id, text=self.java_text,
                                          fill=theme.ACCENT if self.java_ok else theme.DANGER)
            except tk.TclError:
                pass

    # -------------------------------------------------------------- прогресс

    def _paint_bar(self, percent, indeterminate: bool = False):
        cv = self.canvas
        cv.delete("barfill")
        if indeterminate:
            width = BAR_X1 - BAR_X0
            segment = width * 0.32
            span = max(1.0, width - segment)
            offset = (time.time() * 120) % (span * 2)
            if offset > span:
                offset = span * 2 - offset
            x0 = BAR_X0 + offset
            draw.gradient_band(cv, x0, BAR_Y, x0 + segment, BAR_Y + BAR_H, self.bar_accent,
                               draw.mix(self.bar_accent, "#FFFFFF", 0.35), steps=24, tags=("barfill",))
            return
        if percent <= 0:
            return
        width = (BAR_X1 - BAR_X0) * min(100.0, percent) / 100.0
        if width < 2:
            return
        x1 = BAR_X0 + max(6, width)
        draw.gradient_band(cv, BAR_X0, BAR_Y, x1, BAR_Y + BAR_H, self.bar_accent,
                           draw.mix(self.bar_accent, "#FFFFFF", 0.35), steps=36, tags=("barfill",))

    def _poll(self):
        self._drain_queue()
        try:
            snapshot = self.progress.snapshot()
            if self.page == "play" and self.worker and self.worker.is_alive():
                if snapshot.get("indeterminate"):
                    self._paint_bar(0, indeterminate=True)
                    self.canvas.itemconfigure(self.stage_id, text=snapshot["stage"][:30])
                    self.canvas.itemconfigure(self.meta_id, text=snapshot["detail"][:52])
                    self.canvas.itemconfigure(self.pct_id, text="…")
                else:
                    target = snapshot["percent"]
                    self._display_pct += (target - self._display_pct) * 0.4
                    if abs(target - self._display_pct) < 0.5:
                        self._display_pct = target
                    self._paint_bar(self._display_pct)
                    self.canvas.itemconfigure(self.stage_id, text=snapshot["stage"][:30])
                    self.canvas.itemconfigure(
                        self.meta_id,
                        text=(f"{snapshot['done_files']}/{snapshot['total_files']} · "
                              f"{snapshot['downloaded_text']} · {snapshot['speed_text']}"),
                    )
                    self.canvas.itemconfigure(self.pct_id, text=f"{self._display_pct:.0f} %")
        except tk.TclError:
            pass
        try:
            self.after(120, self._poll)
        except tk.TclError:
            pass

    def report_callback_exception(self, exc, val, tb):
        """Ошибка в обработчике tkinter: в лог и одно понятное окно."""
        log.error("ошибка в интерфейсе", exc_info=(exc, val, tb))
        if self._error_shown:
            return
        self._error_shown = True
        try:
            messagebox.showerror(
                "Vleyz Launcher",
                i18n.tr(f"{val}\n\nПодробности: {paths.logs_dir() / 'launcher.log'}"),
            )
        except tk.TclError:
            pass

    def _on_close(self):
        self.cancel_event.set()
        try:
            self.destroy()
        except tk.TclError:
            pass
