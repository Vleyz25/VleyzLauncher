from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vleyzlauncher import APP_VERSION  # noqa: E402
from vleyzlauncher.core import paths, profiles, secure, updates  # noqa: E402


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = mock.patch.dict(os.environ, {"APPDATA": self.temp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def config_file(self) -> Path:
        return Path(self.temp.name) / paths.APP_FOLDER / "config.json"

    def test_save_encrypts_secrets_and_load_restores(self) -> None:
        cfg = dict(paths.CONFIG_DEFAULTS)
        cfg["microsoft_auth"] = {"refresh_token": "refresh-secret", "profile_name": "Vleyz"}
        cfg["ely_auth"] = {"password": "ely-secret", "login": "me"}

        self.assertTrue(paths.save_config(cfg))
        raw = self.config_file().read_text(encoding="utf-8")
        self.assertNotIn("refresh-secret", raw)
        self.assertNotIn("ely-secret", raw)

        loaded = paths.load_config()
        self.assertEqual(loaded["microsoft_auth"]["refresh_token"], "refresh-secret")
        self.assertEqual(loaded["ely_auth"]["password"], "ely-secret")

    def test_legacy_config_migrates(self) -> None:
        legacy = Path(self.temp.name) / "legacy.json"
        legacy.write_text(json.dumps({"nickname": "Old", "ram_mb": 4096}), encoding="utf-8")
        with mock.patch.object(paths, "legacy_config_path", return_value=legacy):
            loaded = paths.load_config()

        self.assertEqual(loaded["nickname"], "Old")
        self.assertEqual(loaded["ram_mb"], 4096)
        self.assertTrue(self.config_file().is_file())
        self.assertTrue(legacy.with_name(legacy.name + ".migrated").is_file())

    def test_unknown_keys_survive(self) -> None:
        path = self.config_file()
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"nickname": "A", "future_key": 42}), encoding="utf-8")
        cfg = paths.load_config()
        self.assertEqual(cfg["future_key"], 42)


class SecureTests(unittest.TestCase):
    @unittest.skipUnless(secure.available(), "DPAPI есть только на Windows")
    def test_roundtrip(self) -> None:
        value = "пароль с пробелами и юникодом"
        encrypted = secure.protect(value)
        self.assertTrue(encrypted.startswith(secure.PREFIX))
        self.assertEqual(secure.unprotect(encrypted), value)

    def test_plain_value_is_left_alone(self) -> None:
        self.assertEqual(secure.protect(""), "")
        self.assertEqual(secure.unprotect("plain"), "plain")


class UpdateTests(unittest.TestCase):
    def test_version_compare(self) -> None:
        self.assertTrue(updates.is_newer("v1.10.0", "1.6.0"))
        self.assertTrue(updates.is_newer("1.6.1", APP_VERSION))
        self.assertFalse(updates.is_newer("v1.5", APP_VERSION))
        self.assertFalse(updates.is_newer("", APP_VERSION))
        self.assertFalse(updates.is_newer("1.6", "1.6.0"))

    def test_prefers_setup_asset(self) -> None:
        payload = {
            "tag_name": "v9.0",
            "html_url": "https://github.com/Vleyz25/VleyzLauncher/releases/tag/v9.0",
            "assets": [
                {"name": "VleyzLauncher.exe", "browser_download_url": "https://example/VleyzLauncher.exe"},
                {"name": "VleyzLauncher-Setup.exe", "browser_download_url": "https://example/Setup.exe"},
            ],
        }
        with mock.patch.object(updates.net, "fetch_json", return_value=payload):
            info = updates.check("1.6.0")
        self.assertIsNotNone(info)
        self.assertTrue(info["setup_url"].endswith("Setup.exe"))

    def test_network_error_is_quiet(self) -> None:
        with mock.patch.object(updates.net, "fetch_json", side_effect=updates.net.DownloadError("нет сети")):
            self.assertIsNone(updates.check("1.6.0"))


class ProfileTests(unittest.TestCase):
    def test_ensure_and_activate(self) -> None:
        cfg = dict(paths.CONFIG_DEFAULTS)
        items = profiles.ensure(cfg)
        self.assertEqual(len(items), 1)
        second = profiles.create(cfg, "Второй", "Alex")
        self.assertTrue(profiles.activate(cfg, second["id"]))
        self.assertEqual(cfg["nickname"], "Alex")
        self.assertTrue(profiles.remove(cfg, second["id"]))
        self.assertEqual(len(profiles.profiles(cfg)), 1)


if __name__ == "__main__":
    unittest.main()
