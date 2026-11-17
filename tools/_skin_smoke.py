"""Курительная проверка модалки скинов без настоящих данных игрока."""
from __future__ import annotations

import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(ROOT))

from vleyzlauncher.core import paths

tmp = Path(tempfile.mkdtemp(prefix="vleyz-skin-"))
(tmp / "state").mkdir(parents=True, exist_ok=True)
(tmp / "data").mkdir(parents=True, exist_ok=True)
paths.state_dir = lambda: tmp / "state"
paths.default_data_dir = lambda: tmp / "data"

import vleyzlauncher.ui.app as app_module

app = app_module.Launcher()
try:
    app.withdraw()
    app._open_skin_modal()
    app.update()
    app._skin_variant("slim")
    app.update()
    print("модалка скинов нарисована без ошибок, формат:", app._skin["variant"])
finally:
    app.destroy()
