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

from vleyzlauncher import APP_VERSION, i18n  # noqa: E402
from vleyzlauncher.core import (  # noqa: E402
    backups,
    instances,
    launch,
    modpacks,
    modrinth,
    mods,
    net,
    packs,
    paths,
    profiles,
    secure,
    social,
    updates,
)


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


class InstanceTests(unittest.TestCase):
    def test_ensure_copies_legacy_data_once(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            (data / "mods").mkdir()
            (data / "mods" / "legacy.jar").write_bytes(b"jar")
            (data / "saves").mkdir()
            (data / "saves" / "world").mkdir()
            (data / "saves" / "world" / "level.dat").write_bytes(b"level")

            target = instances.ensure(data, "1.20.1-fabric")
            self.assertTrue((target / "mods" / "legacy.jar").is_file())
            self.assertTrue((target / "saves" / "world" / "level.dat").is_file())
            self.assertTrue((target / "config").is_dir())

            # повторный ensure не перезаписывает изменения инстанса
            (target / "mods" / "legacy.jar").write_bytes(b"changed")
            instances.ensure(data, "1.20.1-fabric")
            self.assertEqual((target / "mods" / "legacy.jar").read_bytes(), b"changed")

    def test_mods_are_scoped_to_instance(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            target = instances.ensure(data, "1.20.1-fabric")
            (target / "mods" / "a.jar").write_bytes(b"a")
            self.assertEqual([m["name"] for m in mods.list_mods(data, "1.20.1-fabric")], ["a.jar"])
            self.assertEqual(mods.list_mods(data, "1.20.1-forge"), [])
            self.assertTrue(mods.set_enabled(data, "a.jar", False, "1.20.1-fabric"))
            self.assertTrue((target / "mods-disabled" / "a.jar").is_file())


class JvmArgsTests(unittest.TestCase):
    def test_parse_quotes_and_windows_paths(self) -> None:
        parsed = launch.parse_jvm_args('"-Dfoo=bar baz" -XX:+UseG1GC -Dpath=C:\\Games')
        self.assertEqual(parsed[0], "-Dfoo=bar baz")
        self.assertIn("-XX:+UseG1GC", parsed)
        self.assertIn("-Dpath=C:\\Games", parsed)
        self.assertEqual(launch.parse_jvm_args(""), [])

    def test_build_command_uses_extra_args_and_game_dir(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            version = {
                "id": "1.20.1",
                "type": "release",
                "mainClass": "net.minecraft.client.main.Main",
                "assets": "5",
                "assetIndex": {"id": "5"},
                "libraries": [],
                "arguments": {
                    "jvm": ["-cp", "${classpath}"],
                    "game": ["--username", "${auth_player_name}", "--gameDir", "${game_directory}"],
                },
            }
            path = data / "versions" / "1.20.1"
            path.mkdir(parents=True)
            (path / "1.20.1.json").write_text(json.dumps(version), encoding="utf-8")
            game_dir = data / "instances" / "1.20.1"
            command = launch.build_command(
                data, "1.20.1", "Steve", 2048, Path("java.exe"),
                jvm_args="-Dfoo=bar", game_dir=game_dir,
            )
            self.assertIn("-Dfoo=bar", command)
            index = command.index("--gameDir")
            self.assertEqual(Path(command[index + 1]), game_dir)


class ModrinthTests(unittest.TestCase):
    def setUp(self) -> None:
        modrinth._project_cache.clear()

    def test_search_filters_by_game_and_loader(self) -> None:
        seen: list[str] = []
        payload = {"hits": [{
            "project_id": "abc", "slug": "abc", "title": "ABC",
            "description": "test", "downloads": 10, "author": "me",
        }]}

        def fake(url, timeout=30, retries=4):
            seen.append(url)
            return payload

        with mock.patch.object(modrinth.net, "fetch_json", side_effect=fake):
            result = modrinth.search("abc", "1.20.1", "fabric")
        self.assertEqual(result[0]["title"], "ABC")
        self.assertIn("categories%3Afabric", seen[0])
        self.assertIn("versions%3A1.20.1", seen[0])

    def test_resolve_includes_required_dependency(self) -> None:
        root_version = {
            "id": "root-v", "project_id": "root", "version_number": "1.0",
            "version_type": "release", "date_published": "2026-01-02",
            "files": [{"url": "https://example/root.jar", "filename": "root.jar",
                       "primary": True, "size": 10, "hashes": {"sha1": "a"}}],
            "dependencies": [{"project_id": "dep", "dependency_type": "required"}],
        }
        dep_version = {
            "id": "dep-v", "project_id": "dep", "version_number": "2.0",
            "version_type": "release", "date_published": "2026-01-01",
            "files": [{"url": "https://example/dep.jar", "filename": "dep.jar",
                       "primary": True, "size": 20, "hashes": {"sha1": "b"}}],
            "dependencies": [],
        }

        def fake(url, timeout=30, retries=4):
            if "/search" in url:
                return {"hits": []}
            if "/project/root/version" in url:
                return [root_version]
            if "/project/dep/version" in url:
                return [dep_version]
            if "/project/root" in url:
                return {"title": "Root"}
            if "/project/dep" in url:
                return {"title": "Dep"}
            if "/version/" in url:
                return dep_version
            return {}

        with mock.patch.object(modrinth.net, "fetch_json", side_effect=fake):
            plan = modrinth.resolve("root", "1.20.1", "fabric")
        self.assertEqual([item["filename"] for item in plan], ["dep.jar", "root.jar"])
        self.assertTrue(plan[0]["dependency"])

    def test_install_skips_valid_file_and_downloads_missing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder)
            (dest / "dep.jar").write_bytes(b"ok")
            files = [
                {"title": "Dep", "filename": "dep.jar", "url": "https://example/dep.jar",
                 "size": 2, "sha1": None},
                {"title": "Root", "filename": "root.jar", "url": "https://example/root.jar",
                 "size": 3, "sha1": None},
            ]
            with mock.patch.object(modrinth.net, "download") as download:
                result = modrinth.install(files, dest)
            self.assertEqual(result["skipped"], ["dep.jar"])
            self.assertEqual(result["installed"], ["root.jar"])
            download.assert_called_once()


class ModpackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()

    def _zip_with_mods(self) -> Path:
        import zipfile

        archive = self.root / "pack.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("mods/example.jar", b"PK jar")
            handle.writestr("config/example.toml", b"value = 1")
        return archive

    def test_inspect_and_install_plain_zip(self) -> None:
        archive = self._zip_with_mods()
        plan = modpacks.inspect(archive)
        self.assertEqual(plan["kind"], "zip")
        result = modpacks.install(self.data, "1.20.1-fabric", archive)
        self.assertEqual(result["extracted"], 2)
        target = instances.path(self.data, "1.20.1-fabric")
        self.assertTrue((target / "mods" / "example.jar").is_file())
        self.assertTrue((target / "config" / "example.toml").is_file())

    def test_inspect_mrpack_metadata_and_env(self) -> None:
        import zipfile

        archive = self.root / "pack.mrpack"
        index = {
            "formatVersion": 1,
            "name": "Test pack",
            "versionId": "1.0",
            "dependencies": {"minecraft": "1.20.1", "forge": "47.1.0"},
            "files": [
                {"path": "mods/a.jar", "hashes": {"sha1": "aa"}, "fileSize": 5,
                 "downloads": ["https://example.invalid/a.jar"]},
                {"path": "mods/b.jar", "downloads": ["https://example.invalid/b.jar"],
                 "env": {"client": "unsupported"}},
            ],
        }
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("modrinth.index.json", json.dumps(index))
            handle.writestr("overrides/config/pack.txt", b"x")
        plan = modpacks.inspect(archive)
        self.assertEqual(plan["kind"], "modrinth")
        self.assertEqual(plan["game_version"], "1.20.1")
        self.assertEqual(plan["loader"], "forge")
        self.assertEqual([item["path"] for item in plan["files"]], ["mods/a.jar"])

    def test_broken_archive_rejected(self) -> None:
        broken = self.root / "broken.zip"
        broken.write_bytes(b"not a zip at all")
        with self.assertRaises(modpacks.ModpackError):
            modpacks.inspect(broken)


class BackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "data"
        self.world = instances.saves_dir(self.data, "1.20.1")
        self.world.mkdir(parents=True)
        (self.world / "world").mkdir()
        (self.world / "world" / "level.dat").write_bytes(b"level")

    def test_create_restore_and_rotate(self) -> None:
        first = backups.create(self.data, "1.20.1", keep=2)
        self.assertIsNotNone(first)
        for index in range(3):
            import time

            time.sleep(1.05)
            backups.create(self.data, "1.20.1", label=f"n{index}", keep=2)
        self.assertEqual(len(backups.list_backups(self.data, "1.20.1")), 2)

        (self.world / "world" / "level.dat").write_bytes(b"broken")
        backups.restore(self.data, "1.20.1", backups.list_backups(self.data, "1.20.1")[0]["name"])
        self.assertIn((self.world / "world" / "level.dat").read_bytes(), (b"level", b"broken"))

    def test_empty_saves_returns_none(self) -> None:
        empty = Path(self.temp.name) / "empty"
        instances.saves_dir(empty, "1.20.1").mkdir(parents=True)
        self.assertIsNone(backups.create(empty, "1.20.1"))


class PackTests(unittest.TestCase):
    def test_toggle_moves_between_folders(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            target = packs.folder(data, "1.20.1", "resourcepack")
            target.mkdir(parents=True)
            (target / "pack.zip").write_bytes(b"PK")
            self.assertTrue(packs.installed(data, "1.20.1", "resourcepack")[0]["enabled"])
            self.assertTrue(packs.set_enabled(data, "1.20.1", "resourcepack", "pack.zip", False))
            item = packs.installed(data, "1.20.1", "resourcepack")[0]
            self.assertFalse(item["enabled"])
            self.assertTrue(item["path"].is_file())


class MarkerTests(unittest.TestCase):
    def test_new_folder_is_marked_as_launcher_data(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "minecraft"
            self.assertTrue(paths.ensure_data_root(target))
            self.assertTrue((target / paths.DATA_MARKER).is_file())

    def test_foreign_folder_is_not_marked(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "old-minecraft"
            target.mkdir()
            (target / "saves").mkdir()
            (target / "saves" / "world.dat").write_bytes(b"x")
            self.assertFalse(paths.ensure_data_root(target))
            self.assertFalse((target / paths.DATA_MARKER).is_file())

    def test_empty_folder_is_marked(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "empty"
            target.mkdir()
            self.assertTrue(paths.ensure_data_root(target))
            self.assertTrue(paths.owned_by_launcher(target, paths.DATA_MARKER))

    def test_install_marker_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "app"
            paths.mark_install(target)
            self.assertTrue(paths.owned_by_launcher(target))
            self.assertFalse(paths.owned_by_launcher(Path(folder)))


class InstallerSafetyTests(unittest.TestCase):
    def _module(self):
        import importlib.util

        path = ROOT / "tools" / "installer" / "setup.py"
        spec = importlib.util.spec_from_file_location("vleyz_setup_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_uninstaller_never_deletes_folder_recursively(self) -> None:
        module = self._module()
        template = module.UNINSTALL_TEMPLATE
        self.assertNotIn('rmdir /s /q "{folder}"', template)
        self.assertNotIn('rmdir /s /q "{folder}\\\\"', template)
        self.assertIn("{marker}", template)
        self.assertIn("{datamarker}", template)

    def test_uninstaller_checks_marker_before_deleting(self) -> None:
        module = self._module()
        template = module.UNINSTALL_TEMPLATE
        marker_line = next(
            (line for line in template.splitlines() if "if not exist" in line and "{marker}" in line),
            "",
        )
        self.assertIn("goto foreign", marker_line)


class I18nTests(unittest.TestCase):
    def test_english_translation_and_patterns(self) -> None:
        previous = i18n.CURRENT
        try:
            i18n.set_language("en")
            self.assertEqual(i18n.tr("Главная"), "Home")
            self.assertEqual(i18n.tr("Установлена · 1,2 ГБ"), "Installed · 1,2 ГБ")
            self.assertEqual(i18n.tr("Найдено: 5"), "Found: 5")
            i18n.set_language("ru")
            self.assertEqual(i18n.tr("Главная"), "Главная")
        finally:
            i18n.set_language(previous)


if __name__ == "__main__":
    unittest.main()
