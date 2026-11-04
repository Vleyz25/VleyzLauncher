from __future__ import annotations

import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vleyzlauncher import APP_VERSION  # noqa: E402
from vleyzlauncher.core import launch, net, paths, profiles, secure, social, updates  # noqa: E402


class RangeHandler(http.server.BaseHTTPRequestHandler):
    payload = b""
    ranges: list[str | None] = []
    support_range = True

    def do_GET(self) -> None:  # noqa: N802 - имя задаёт http.server
        type(self).ranges.append(self.headers.get("Range"))
        start = 0
        header = self.headers.get("Range")
        if self.support_range and header and header.startswith("bytes="):
            start = int(header.split("=", 1)[1].split("-", 1)[0])
        body = self.payload[start:]
        self.send_response(206 if start else 200)
        if start:
            self.send_header("Content-Range", f"bytes {start}-{len(self.payload) - 1}/{len(self.payload)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


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
        self.assertTrue(updates.is_newer("1.6.1", "1.6.0"))
        self.assertFalse(updates.is_newer("v1.5", "1.6.0"))
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

    def test_install_command_is_silent_and_targeted(self) -> None:
        command = updates.install_command(Path("C:/Temp/Setup.exe"), Path("C:/Apps/Vleyz"))
        self.assertEqual(command[0], "cmd")
        line = command[2]
        self.assertIn("--silent", line)
        self.assertIn("--dir", line)
        self.assertIn("--no-shortcuts", line)
        self.assertIn("Setup.exe", line)


class DownloadTests(unittest.TestCase):
    def serve(self, handler) -> str:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}/file.bin"

    def test_resume_uses_range_and_counts_offset(self) -> None:
        payload = bytes(range(256)) * 400
        RangeHandler.payload = payload
        RangeHandler.ranges = []
        RangeHandler.support_range = True
        url = self.serve(RangeHandler)

        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "file.bin"
            dest.with_name("file.bin.part").write_bytes(payload[:40000])
            calls: list[int] = []
            net.download(url, dest, size=len(payload), on_bytes=calls.append)

            self.assertEqual(dest.read_bytes(), payload)
            self.assertFalse(dest.with_name("file.bin.part").exists())
            self.assertIn("bytes=40000-", RangeHandler.ranges)
            self.assertEqual(sum(calls), len(payload))

    def test_server_without_range_restarts_cleanly(self) -> None:
        class PlainHandler(RangeHandler):
            support_range = False

        payload = b"abcdef" * 20000
        PlainHandler.payload = payload
        PlainHandler.ranges = []
        url = self.serve(PlainHandler)

        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "file.bin"
            dest.with_name("file.bin.part").write_bytes(payload[:30000])
            calls: list[int] = []
            net.download(url, dest, size=len(payload), on_bytes=calls.append)

            self.assertEqual(dest.read_bytes(), payload)
            self.assertEqual(sum(calls), len(payload))


class SocialTests(unittest.TestCase):
    def test_privileges_stub_allows_multiplayer(self) -> None:
        payload = net.fetch_json(social.ensure_server() + "/privileges")
        self.assertTrue(payload["privileges"]["multiplayerServer"]["enabled"])
        self.assertTrue(payload["privileges"]["onlineChat"]["enabled"])
        self.assertTrue(payload["privileges"]["multiplayerRealms"]["enabled"])

    def test_blocklist_is_empty(self) -> None:
        payload = net.fetch_json(social.ensure_server() + "/privacy/blocklist")
        self.assertEqual(payload["blockedProfiles"], [])

    def test_jvm_args_override_services_only(self) -> None:
        args = social.jvm_args()
        self.assertEqual(len(args), 4)
        self.assertTrue(
            any(a.startswith("-Dminecraft.api.services.host=http://127.0.0.1:") for a in args)
        )
        self.assertIn("-Dminecraft.api.auth.host=https://authserver.mojang.com", args)
        self.assertIn("-Dminecraft.api.account.host=https://api.mojang.com", args)
        self.assertIn("-Dminecraft.api.session.host=https://sessionserver.mojang.com", args)


class LaunchTests(unittest.TestCase):
    def test_offline_and_ely_enable_multiplayer(self) -> None:
        self.assertEqual(launch.user_type(None), "mojang")
        self.assertEqual(launch.user_type({"user_type": "ely"}), "mojang")
        self.assertEqual(launch.user_type({"user_type": "legacy"}), "mojang")

    def test_microsoft_keeps_msa(self) -> None:
        self.assertEqual(launch.user_type({"user_type": "msa"}), "msa")


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
