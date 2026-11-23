from __future__ import annotations

import http.server
import json
import logging
import os
import sqlite3
import ssl
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vleyzlauncher import APP_VERSION, i18n  # noqa: E402
from vleyzlauncher.core import (  # noqa: E402
    backups,
    builder,
    cache as cache_core,
    catalog,
    cleanup,
    config_check,
    crash,
    diagnostics,
    events,
    fsutil,
    gigachat,
    installer,
    instances,
    java,
    jobs,
    launch,
    logging_setup,
    modpacks,
    modlist,
    modmeta,
    modpack_source,
    modrinth,
    mods,
    modupdates,
    net,
    news,
    packs,
    pack_export,
    paths,
    plugins,
    profiles,
    safepath,
    secure,
    servers,
    skins,
    social,
    telemetry,
    updates,
    versions,
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

    def test_secret_encryption_failure_keeps_previous_config(self) -> None:
        path = self.config_file()
        path.parent.mkdir(parents=True)
        path.write_text('{"existing": true}', encoding="utf-8")
        cfg = dict(paths.CONFIG_DEFAULTS)
        cfg["microsoft_auth"] = {"refresh_token": "secret-value"}
        with mock.patch.object(secure, "available", return_value=True), \
                mock.patch.object(secure, "_crypt", side_effect=OSError("DPAPI error")):
            self.assertFalse(paths.save_config(cfg))
        self.assertEqual(path.read_text(encoding="utf-8"), '{"existing": true}')


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

    def test_encryption_failure_does_not_return_plaintext(self) -> None:
        with mock.patch.object(secure, "available", return_value=True), \
                mock.patch.object(secure, "_crypt", side_effect=OSError("DPAPI error")):
            with self.assertRaises(OSError):
                secure.protect("secret-value")


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


class GigaChatTlsTests(unittest.TestCase):
    def test_tls_error_does_not_retry_without_certificate_verification(self) -> None:
        client = gigachat.GigaChat("client", "secret")
        request = mock.Mock()
        tls_error = urllib.error.URLError(ssl.SSLCertVerificationError("untrusted"))
        with mock.patch.object(gigachat.urllib.request, "urlopen", side_effect=tls_error) as open_url:
            with self.assertRaises(urllib.error.URLError):
                client._open(request)
        self.assertEqual(open_url.call_count, 1)
        self.assertFalse(hasattr(client, "_insecure"))


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


class LaunchLoggingTests(unittest.TestCase):
    def test_access_token_is_redacted_from_launch_log(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            version_id = "1.20.1"
            version_dir = data / "versions" / version_id
            version_dir.mkdir(parents=True)
            version = {
                "id": version_id,
                "mainClass": "example.Main",
                "libraries": [],
                "arguments": {
                    "jvm": ["-cp", "${classpath}"],
                    "game": ["--accessToken", "${auth_access_token}"],
                },
            }
            (version_dir / f"{version_id}.json").write_text(
                json.dumps(version), encoding="utf-8"
            )
            session = {"access_token": "sensitive-token-value", "profile_name": "Player"}
            with mock.patch.object(launch.subprocess, "Popen", return_value=mock.Mock()):
                _process, log_path = launch.launch(
                    data, version_id, "Player", 2048, Path("java.exe"), session=session
                )
            content = log_path.read_text(encoding="utf-8")
            self.assertNotIn("sensitive-token-value", content)
            self.assertIn("***", content)


class JavaArchiveSafetyTests(unittest.TestCase):
    def test_extract_rejects_zip_slip_without_writing_outside(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive_path = root / "java.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("jre/bin/javaw.exe", b"java")
                archive.writestr("jre/../../outside.txt", b"outside")
            target = root / "extracted"
            with self.assertRaises(safepath.PathError):
                java._extract(archive_path, target)
            self.assertFalse((root / "outside.txt").exists())

    def test_invalid_archive_preserves_existing_java(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive_path = root / "bad-java.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../outside.txt", b"outside")
            target = root / "java"
            target.mkdir()
            (target / "existing.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(safepath.PathError):
                java._extract(archive_path, target)
            self.assertEqual((target / "existing.txt").read_text(encoding="utf-8"), "keep")
            self.assertFalse((root / "outside.txt").exists())


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
            def fake_download(url, target, *args, **kwargs):
                Path(target).write_bytes(b"ok")

            with mock.patch.object(modrinth.net, "download",
                                   side_effect=fake_download) as download:
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
        self.assertNotIn('rmdir /s /q "%VL_DIR%"', template)
        self.assertIn('rmdir "%VL_DIR%" 2>nul', template)
        self.assertIn("{marker}", template)
        self.assertIn("{datamarker}", template)

    def test_uninstaller_refuses_system_folders(self) -> None:
        module = self._module()
        template = module.UNINSTALL_TEMPLATE
        self.assertIn(":unsafe", template)
        self.assertIn('"%VL_CHECK:~-1%"==":"', template)
        self.assertIn("if /i \"%VL_CHECK%\"==\"%USERPROFILE%\" goto unsafe", template)
        self.assertIn("ProgramFiles", template)

    def test_uninstaller_deletes_only_known_files(self) -> None:
        module = self._module()
        template = module.UNINSTALL_TEMPLATE
        self.assertIn('del "%VL_DIR%\\{exe}"', template)
        self.assertIn('del "%VL_DIR%\\{uninstaller}"', template)
        self.assertNotIn('del "%VL_DIR%\\*.', template)

    def test_uninstaller_skips_real_jdk_folder(self) -> None:
        module = self._module()
        template = module.UNINSTALL_TEMPLATE
        self.assertIn('if not exist "%VL_DIR%\\java\\bin\\java.exe"', template)

    def test_temp_path_without_trailing_slash(self) -> None:
        module = self._module()
        self.assertEqual(module.normalize_temp("C:\\Temp\\"), "C:\\Temp")
        self.assertEqual(module.normalize_temp("C:\\Temp"), "C:\\Temp")
        self.assertEqual(module.normalize_temp(""), ".")

    def test_unsafe_install_reason(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.dict(os.environ, {"USERPROFILE": folder}):
            self.assertTrue(module.unsafe_install_reason(Path(folder)))
            self.assertEqual(
                module.unsafe_install_reason(Path(folder) / "Games" / "VleyzLauncher"), ""
            )
        self.assertTrue(module.unsafe_install_reason(Path("C:/")))

    def test_layout_keeps_everything_in_one_folder(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "VleyzLauncher"
            target.mkdir()
            module.ensure_layout(target)
            for name in ("minecraft", "java", "logs"):
                self.assertTrue((target / name).is_dir(), name)
            self.assertTrue((target / "minecraft" / module.DATA_MARKER).is_file())
            module.ensure_layout(target)
            self.assertTrue((target / "minecraft" / module.DATA_MARKER).is_file())

    def test_uninstaller_checks_marker_before_deleting(self) -> None:
        module = self._module()
        template = module.UNINSTALL_TEMPLATE
        marker_line = next(
            (line for line in template.splitlines() if "if not exist" in line and "{marker}" in line),
            "",
        )
        self.assertIn("goto foreign", marker_line)


class PortablePathsTests(unittest.TestCase):
    def test_marker_moves_game_data_next_to_exe(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / "VleyzLauncher"
            app.mkdir()
            profile = Path(folder) / "profile"
            with mock.patch.object(paths, "app_dir", return_value=app), \
                    mock.patch.dict(os.environ, {"APPDATA": str(profile)}):
                self.assertFalse(paths.is_portable())
                self.assertEqual(
                    paths.default_data_dir(), profile / "VleyzLauncher" / "minecraft"
                )
                (app / paths.PORTABLE_MARKER).write_text("{}", encoding="utf-8")
                self.assertTrue(paths.is_portable())
                self.assertEqual(paths.default_data_dir(), app / "minecraft")
                self.assertEqual(paths.java_root(), app / "java")
                self.assertEqual(paths.logs_dir(), app / "logs")


class InstallerLayoutTests(unittest.TestCase):
    def _module(self):
        import importlib.util

        path = ROOT / "tools" / "installer" / "setup.py"
        spec = importlib.util.spec_from_file_location("vleyz_setup_layout", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_creates_launcher_subfolder(self) -> None:
        module = self._module()
        self.assertEqual(
            module.resolve_target_dir(Path("D:/Games")),
            Path("D:/Games") / "VleyzLauncher",
        )

    def test_keeps_existing_launcher_folder(self) -> None:
        module = self._module()
        self.assertEqual(
            module.resolve_target_dir(Path("D:/Games/VleyzLauncher")),
            Path("D:/Games/VleyzLauncher"),
        )

    def test_keeps_marked_folder(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / module.INSTALL_MARKER).write_text("{}", encoding="utf-8")
            self.assertEqual(module.resolve_target_dir(target), target)

    def test_uninstaller_checks_data_marker_inside_minecraft(self) -> None:
        module = self._module()
        template = module.UNINSTALL_TEMPLATE
        self.assertIn('%VL_DIR%\\minecraft\\{datamarker}', template)
        self.assertIn('{portable}', template)


class PortableConfigTests(unittest.TestCase):
    def _env(self, folder: Path):
        app = folder / "VleyzLauncher"
        app.mkdir()
        profile = folder / "profile"
        (profile / "VleyzLauncher").mkdir(parents=True)
        (app / paths.PORTABLE_MARKER).write_text("{}", encoding="utf-8")
        return app, profile

    def test_portable_wins_over_old_absolute_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app, profile = self._env(root)
            legacy = profile / "VleyzLauncher" / "minecraft"
            cfg_file = profile / "VleyzLauncher" / "config.json"
            cfg_file.write_text(json.dumps({"data_dir": str(legacy)}), encoding="utf-8")
            with mock.patch.object(paths, "app_dir", return_value=app), \
                    mock.patch.dict(os.environ, {"APPDATA": str(profile)}):
                cfg = paths.load_config()
            self.assertEqual(Path(cfg["data_dir"]), app / "minecraft")

    def test_custom_data_dir_kept_in_portable(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app, profile = self._env(root)
            custom = root / "my-worlds"
            cfg_file = profile / "VleyzLauncher" / "config.json"
            cfg_file.write_text(json.dumps({"data_dir": str(custom)}), encoding="utf-8")
            with mock.patch.object(paths, "app_dir", return_value=app), \
                    mock.patch.dict(os.environ, {"APPDATA": str(profile)}):
                cfg = paths.load_config()
            self.assertEqual(Path(cfg["data_dir"]), custom)

    def test_data_dir_ignores_stale_legacy_path(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app, profile = self._env(root)
            legacy = str(profile / "VleyzLauncher" / "minecraft")
            with mock.patch.object(paths, "app_dir", return_value=app), \
                    mock.patch.dict(os.environ, {"APPDATA": str(profile)}):
                self.assertEqual(paths.data_dir({"data_dir": legacy}), app / "minecraft")
                self.assertEqual(paths.data_dir({}), app / "minecraft")

    def test_migration_moves_old_game_folder(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app, profile = self._env(root)
            old = profile / "VleyzLauncher" / "minecraft"
            (old / "saves" / "world").mkdir(parents=True)
            (old / "saves" / "world" / "level.dat").write_bytes(b"level")
            with mock.patch.object(paths, "app_dir", return_value=app), \
                    mock.patch.dict(os.environ, {"APPDATA": str(profile)}):
                moved = paths.migrate_portable_data()
            self.assertIsNotNone(moved)
            self.assertTrue((app / "minecraft" / "saves" / "world" / "level.dat").is_file())
            self.assertFalse(old.exists())


class SafePathTests(unittest.TestCase):
    def test_sanitize_reserved_and_bad_chars(self) -> None:
        self.assertEqual(safepath.sanitize_name("CON.txt"), "_CON.txt")
        self.assertEqual(safepath.sanitize_name("a<b>c.jar"), "a_b_c.jar")
        self.assertEqual(safepath.sanitize_name("  ..  "), "file")
        self.assertEqual(safepath.sanitize_name(""), "file")

    def test_safe_relative_rejects_escapes(self) -> None:
        self.assertIsNone(safepath.safe_relative("../evil.txt"))
        self.assertIsNone(safepath.safe_relative("/etc/passwd"))
        self.assertIsNone(safepath.safe_relative("mods/../../x.jar"))
        self.assertEqual(str(safepath.safe_relative("mods/ok.jar")), "mods/ok.jar")

    def test_extract_zip_skips_escape(self) -> None:
        import zipfile

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive_path = root / "pack.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../evil.txt", b"bad")
                archive.writestr("ok.txt", b"good")
            target = root / "target"
            with zipfile.ZipFile(archive_path) as archive:
                report = safepath.extract_zip(archive, archive.namelist(), target)
            self.assertEqual(report["written"], 1)
            self.assertGreaterEqual(report["skipped"], 1)
            self.assertTrue((target / "ok.txt").is_file())
            self.assertFalse((root / "evil.txt").exists())

    def test_extract_zip_limits_files(self) -> None:
        import zipfile

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive_path = root / "many.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for index in range(3):
                    archive.writestr(f"f{index}.txt", b"x")
            with zipfile.ZipFile(archive_path) as archive:
                with self.assertRaises(safepath.LimitError):
                    safepath.extract_zip(archive, archive.namelist(), root / "out",
                                         max_files=2)

    def test_require_space_raises(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(safepath, "free_space", return_value=10):
                with self.assertRaises(safepath.SpaceError):
                    safepath.require_space(Path(folder), 1000)


class RedactionTests(unittest.TestCase):
    def test_redact_json_and_query(self) -> None:
        self.assertNotIn("abc123", logging_setup.redact('{"access_token": "abc123"}'))
        self.assertIn("***", logging_setup.redact('{"access_token": "abc123"}'))
        self.assertNotIn("hunter2", logging_setup.redact("password=hunter2&x=1"))
        self.assertIn("Bearer ***", logging_setup.redact("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9"))

    def test_filter_cleans_record(self) -> None:
        record = logging.LogRecord("vleyz", logging.INFO, __file__, 1,
                                   'token="secret-value"', (), None)
        self.assertTrue(logging_setup.RedactFilter().filter(record))
        self.assertNotIn("secret-value", record.getMessage())


class ConfigMigrationTests(unittest.TestCase):
    def _load(self, folder: Path, stored: dict) -> dict:
        profile = folder / "profile"
        (profile / "VleyzLauncher").mkdir(parents=True, exist_ok=True)
        (profile / "VleyzLauncher" / "config.json").write_text(
            json.dumps(stored), encoding="utf-8"
        )
        with mock.patch.dict(os.environ, {"APPDATA": str(profile)}):
            return paths.load_config()

    def test_old_config_is_migrated_and_backed_up(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            saved = {"nickname": "Alex", "ram_mb": "4096", "theme": "weird",
                     "profiles": "broken"}
            cfg = self._load(root, saved)
            self.assertEqual(cfg["config_version"], paths.CONFIG_VERSION)
            self.assertEqual(cfg["ram_mb"], 4096)
            self.assertEqual(cfg["theme"], "dark")
            self.assertEqual(cfg["profiles"], [])
            self.assertEqual(cfg["nickname"], "Alex")
            backups = list((root / "profile" / "VleyzLauncher").glob("config.json.backup-*"))
            self.assertTrue(backups)

    def test_current_config_not_touched(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            saved = {"config_version": paths.CONFIG_VERSION, "nickname": "Alex"}
            self._load(root, saved)
            backups = list((root / "profile" / "VleyzLauncher").glob("config.json.backup-*"))
            self.assertFalse(backups)


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


class CatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog.clear_cache()

    def test_facets_for_loader_category_and_environment(self) -> None:
        facets = catalog.build_facets("1.20.1", "fabric", "optimization", "client")
        self.assertIn(["project_type:mod"], facets)
        self.assertIn(["versions:1.20.1"], facets)
        self.assertIn(["categories:fabric"], facets)
        self.assertIn(["categories:optimization"], facets)
        self.assertIn(["client_side:required", "client_side:optional"], facets)
        self.assertEqual(catalog.build_facets(loader="vanilla"), [["project_type:mod"]])

    def test_search_normalizes_hits_pages_and_caches(self) -> None:
        payload = {
            "hits": [{
                "project_id": "abc", "slug": "sodium", "title": "Sodium",
                "description": "Fast", "downloads": 10, "categories": ["fabric"],
                "icon_url": "", "author": "jelly",
            }],
            "total_hits": 25,
        }
        with mock.patch.object(catalog, "_call", return_value=payload) as call:
            first = catalog.search("fast", "1.20.1", "fabric", page=1, page_size=10)
            second = catalog.search("fast", "1.20.1", "fabric", page=1, page_size=10)
        self.assertEqual(first["total"], 25)
        self.assertEqual(first["pages"], 3)
        self.assertEqual(first["page"], 1)
        self.assertEqual(first["items"][0]["id"], "abc")
        self.assertEqual(first["items"][0]["url"], "https://modrinth.com/mod/sodium")
        self.assertEqual(call.call_count, 1)
        self.assertEqual(second["items"], first["items"])

    def test_search_ignores_unknown_sort(self) -> None:
        with mock.patch.object(catalog, "_call",
                               return_value={"hits": [], "total_hits": 0}) as call:
            catalog.search(sort="../../etc")
        params = call.call_args.args[1]
        self.assertEqual(params["index"], "relevance")

    def test_versions_accept_plain_list(self) -> None:
        versions = [
            {"id": "v1", "version_number": "1.0", "date_published": "2024-01-01",
             "files": [{"url": "u", "filename": "a.jar", "size": 5, "primary": True}]},
            {"id": "v2", "version_number": "2.0", "date_published": "2025-01-01",
             "files": [{"url": "u2", "filename": "b.jar", "size": 6}]},
        ]
        with mock.patch.object(catalog, "_call", return_value=versions):
            rows = catalog.project_versions("abc", "1.20.1", "fabric")
        self.assertEqual([row["id"] for row in rows], ["v2", "v1"])
        self.assertEqual(rows[0]["filename"], "b.jar")

    def test_details_cleans_html_markdown_and_urls(self) -> None:
        payload = {
            "title": "Mod",
            "body": "<p>Hi</p> ![x](http://i) [site](http://s) "
                    "<style>a{}</style> https://x.y",
            "downloads": 5,
        }
        with mock.patch.object(catalog, "_call", return_value=payload):
            detail = catalog.details("abc")
        self.assertIn("Hi", detail["body"])
        self.assertIn("site", detail["body"])
        self.assertNotIn("<", detail["body"])
        self.assertNotIn("http", detail["body"])

    def test_clean_text_strips_formatting(self) -> None:
        self.assertEqual(catalog.clean_text("§aHello **world** <b>bold</b>"),
                         "Hello world bold")

    def test_display_category_skips_loader_tags(self) -> None:
        self.assertEqual(catalog.display_category({"categories": ["fabric", "magic"]}),
                         "Магия")
        self.assertEqual(catalog.display_category({"categories": ["fabric"]}), "")


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = cache_core.DiskCache(Path(self.temp.name) / "cache.db")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_set_get_and_clear(self) -> None:
        self.store.set("key", {"a": 1})
        self.assertEqual(self.store.get("key"), {"a": 1})
        self.store.clear()
        self.assertIsNone(self.store.get("key"))

    def test_expired_entry_is_dropped(self) -> None:
        self.store.set("key", "value")
        connection = sqlite3.connect(self.store.path)
        try:
            connection.execute("UPDATE cache SET ts = ts - 10000")
            connection.commit()
        finally:
            connection.close()
        self.assertIsNone(self.store.get("key", ttl=300))
        self.store.set("fresh", "value")
        self.assertEqual(self.store.get("fresh", ttl=0), "value")

    def test_non_json_value_is_ignored(self) -> None:
        self.store.set("bad", object())
        self.assertIsNone(self.store.get("bad"))


class BuilderTests(unittest.TestCase):
    def test_rules_understand_russian(self) -> None:
        intent = builder.parse_intent("хочу pvp сборку на слабый ноут, 20 модов, с шейдерами")
        self.assertIn("optimization", intent["categories"])
        self.assertEqual(intent["limit"], 20)
        self.assertTrue(intent["shaders"])
        self.assertEqual(intent["title"], "PvP")

    def test_empty_query_gives_default(self) -> None:
        intent = builder.parse_intent("")
        self.assertEqual(intent["categories"], ["utility", "optimization", "decoration"])
        self.assertFalse(intent["matched"])
        self.assertEqual(intent["title"], "Vanilla+")
        self.assertEqual(intent["source"], "rules")

    def test_subject_mentioned_first_wins(self) -> None:
        intent = builder.parse_intent("магия для слабого ноутбука")
        self.assertEqual(intent["categories"][0], "magic")
        self.assertIn("optimization", intent["categories"])
        self.assertTrue(intent["matched"])

    def test_rules_understand_categories(self) -> None:
        cases = {
            "сборка про космос": "worldgen",
            "драконы и зомби": "mobs",
            "средневековая деревня": "adventure",
            "поезда и машины": "transportation",
            "техно и заводы": "technology",
            "красивый дом и мебель": "decoration",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(builder.parse_intent(query)["categories"][0], expected)

    def test_unknown_query_is_not_optimization(self) -> None:
        intent = builder.parse_intent("хочу что-то необычное")
        self.assertFalse(intent["matched"])
        self.assertNotIn("optimization", intent["categories"][:1])
        self.assertEqual(intent["title"], "Vanilla+")

    def test_limit_is_clamped(self) -> None:
        self.assertEqual(builder.clamp_limit(500), builder.MAX_LIMIT)
        self.assertEqual(builder.clamp_limit(1), builder.MIN_LIMIT)
        self.assertEqual(builder.clamp_limit("nope"), builder.DEFAULT_LIMIT)

    def test_intent_from_json_skips_unknown_categories(self) -> None:
        intent = builder.intent_from_json(
            '```json\n{"categories": ["magic", "bad"], "limit": 99, "title": "Магия"}\n```')
        self.assertEqual(intent["categories"], ["magic"])
        self.assertEqual(intent["limit"], builder.MAX_LIMIT)
        self.assertEqual(intent["source"], "gigachat")

    def test_broken_json_raises(self) -> None:
        with self.assertRaises(builder.BuilderError):
            builder.intent_from_json("тут нет json")
        with self.assertRaises(builder.BuilderError):
            builder.intent_from_json('{"categories": ["bad"], "limit": 10}')

    def test_preset_intent(self) -> None:
        intent = builder.preset_intent("pvp")
        self.assertEqual(intent["categories"], ["optimization", "equipment", "utility"])
        self.assertEqual(intent["source"], "preset")
        with self.assertRaises(builder.BuilderError):
            builder.preset_intent("nope")

    def test_select_dedupes_and_limits(self) -> None:
        hits = [{"id": "a", "title": "A", "downloads": 10},
                {"id": "b", "title": "B", "downloads": 9}]
        with mock.patch.object(builder.catalog, "search", return_value={"items": hits}):
            items = builder.select({"categories": ["optimization"], "limit": 5},
                                   "1.20.1", "fabric")
        self.assertEqual([row["id"] for row in items], ["a", "b"])
        self.assertEqual(items[0]["reason"], "Оптимизация")

    def test_select_rejects_vanilla(self) -> None:
        with self.assertRaises(builder.BuilderError):
            builder.select({"categories": ["magic"], "limit": 5}, "1.20.1", "vanilla")

    def test_backup_mods(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            mods = root / "mods"
            mods.mkdir()
            (mods / "a.jar").write_bytes(b"jar")
            target = builder.backup_mods(mods, root / "backups")
            self.assertIsNotNone(target)
            self.assertTrue(target.is_file())
            with zipfile.ZipFile(target) as archive:
                self.assertEqual(archive.namelist(), ["a.jar"])
            self.assertIsNone(builder.backup_mods(root / "empty", root / "backups"))


class PluginTests(unittest.TestCase):
    def test_load_valid_and_skip_broken(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with mock.patch.object(plugins, "plugins_dir", return_value=root):
                (root / "mine.json").write_text(json.dumps({
                    "name": "Мои сборки",
                    "version": 1,
                    "presets": [
                        {"id": "skyblock", "title": "SkyBlock",
                         "categories": ["adventure", "utility"], "limit": 14},
                        {"id": "bad id!", "title": "Плохой",
                         "categories": ["magic"]},
                        {"id": "empty", "title": "Пусто", "categories": ["nope"]},
                    ],
                }, ensure_ascii=False), encoding="utf-8")
                (root / "broken.json").write_text("{oops", encoding="utf-8")
                rows = plugins.load()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "Мои сборки:skyblock")
        self.assertEqual(rows[0]["limit"], 14)

    def test_write_example(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with mock.patch.object(plugins, "plugins_dir", return_value=root):
                path = plugins.write_example()
                self.assertTrue(path.is_file())
                self.assertEqual(len(plugins.load()), 2)


class DiagnosticsTests(unittest.TestCase):
    def test_snapshot_hides_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            cfg = {
                "data_dir": folder,
                "ely_auth": {"password": "hunter2"},
                "gigachat": {"client_id": "id", "client_secret": "topsecret",
                             "enabled": True, "model": "GigaChat"},
            }
            text = json.dumps(diagnostics.snapshot(cfg), ensure_ascii=False)
        self.assertNotIn("hunter2", text)
        self.assertNotIn("topsecret", text)
        self.assertIn('"has_keys": true', text)

    def test_export_contains_report(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = diagnostics.export({"data_dir": str(root / "game")},
                                        root / "report.zip")
            self.assertTrue(target.is_file())
            with zipfile.ZipFile(target) as archive:
                self.assertIn("report.json", archive.namelist())


class ModListTests(unittest.TestCase):
    def test_export_text_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "game"
            instance = "1.20.1-fabric"
            mods_dir = data / "instances" / instance / "mods"
            mods_dir.mkdir(parents=True)
            (mods_dir / "sodium.jar").write_bytes(b"x" * 10)
            rows = modlist.collect(data, instance)
            self.assertEqual(rows["total"], 1)
            text = modlist.export_text(data, instance, root / "list.txt")
            self.assertIn("sodium.jar", text.read_text(encoding="utf-8"))
            target = modlist.export_json(data, instance, root / "list.json")
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["total"], 1)


class HealthIssueTests(unittest.TestCase):
    def test_duplicates_and_vanilla_are_reported(self) -> None:
        items = [{"name": "same.jar", "enabled": True},
                 {"name": "same.jar", "enabled": False}]
        rows = [{"id": "1.20.1-fabric", "game": "1.20.1", "loader": "fabric"}]
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(diagnostics.paths, "data_dir",
                                   return_value=Path(folder)), \
                 mock.patch.object(diagnostics.loaders, "installed_versions",
                                   return_value=rows), \
                 mock.patch.object(diagnostics.mods_core, "list_mods",
                                   return_value=items):
                issues = diagnostics.instance_issues({}, "1.20.1-fabric")
        self.assertTrue(any("Дублируется" in text for text in issues))

    def test_missing_instance(self) -> None:
        with mock.patch.object(diagnostics.loaders, "installed_versions",
                               return_value=[]):
            self.assertEqual(diagnostics.instance_issues({}, "nope"), ["Сборка не найдена"])


class CatalogOfflineTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog.clear_cache()

    def test_stale_page_is_used_when_network_fails(self) -> None:
        stale = {"hits": [{"project_id": "abc", "slug": "x", "title": "Cached",
                           "downloads": 1}], "total_hits": 1}
        with mock.patch.object(catalog, "_call",
                               side_effect=modrinth.ModrinthError("нет сети")), \
             mock.patch.object(catalog, "_stale", return_value=stale):
            result = catalog.search("q", "1.20.1", "fabric")
        self.assertTrue(result["offline"])
        self.assertEqual(result["items"][0]["title"], "Cached")

    def test_error_is_raised_without_cache(self) -> None:
        with mock.patch.object(catalog, "_call",
                               side_effect=modrinth.ModrinthError("нет сети")), \
             mock.patch.object(catalog, "_stale", return_value=None):
            with self.assertRaises(modrinth.ModrinthError):
                catalog.search("q", "1.20.1", "fabric")

    def test_select_skip_shifts_window(self) -> None:
        hits = [{"id": str(index), "title": str(index), "downloads": 10 - index}
                for index in range(6)]
        with mock.patch.object(builder.catalog, "search", return_value={"items": hits}):
            first = builder.select({"categories": ["optimization"], "limit": 5},
                                   "1.20.1", "fabric")
            shifted = builder.select({"categories": ["optimization"], "limit": 5},
                                     "1.20.1", "fabric", skip=2)
        self.assertEqual(first[0]["id"], "0")
        self.assertEqual(shifted[0]["id"], "2")


class CrashTests(unittest.TestCase):
    def test_analyze_extracts_summary_cause_and_mods(self) -> None:
        text = ("Description: Ticking entity\n"
                "Caused by: java.lang.NullPointerException\n"
                "Caused by: net.example.Boom\n"
                "Suspected Mods: sodium, ferritecore\n")
        result = crash.analyze(text)
        self.assertEqual(result["summary"], "Ticking entity")
        self.assertEqual(result["caused"],
                         ["java.lang.NullPointerException", "net.example.Boom"])
        self.assertEqual(result["mods"], ["sodium", "ferritecore"])

    def test_scan_without_reports(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(crash.scan(folder))

    def test_scan_picks_latest_report(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            reports = root / "crash-reports"
            reports.mkdir()
            old = reports / "old.txt"
            new = reports / "new.txt"
            old.write_text("Description: old", encoding="utf-8")
            new.write_text("Description: fresh", encoding="utf-8")
            old_time = new.stat().st_mtime - 100
            os.utime(old, (old_time, old_time))
            result = crash.scan(root)
        self.assertEqual(result["name"], "new.txt")
        self.assertEqual(result["summary"], "fresh")


class PackExportTests(unittest.TestCase):
    def test_index_and_overrides(self) -> None:
        version = {"files": [{
            "url": "https://cdn.example/known.jar",
            "filename": "known.jar",
            "size": 3,
            "hashes": {"sha1": "aaa", "sha512": "ccc"},
            "primary": True,
        }]}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "game"
            instance = "1.20.1-fabric"
            mods_dir = data / "instances" / instance / "mods"
            mods_dir.mkdir(parents=True)
            (mods_dir / "known.jar").write_bytes(b"abc")
            (mods_dir / "local.jar").write_bytes(b"xyz")
            hashes = iter(["aaa", "bbb"])
            with mock.patch.object(pack_export.net, "sha1_file",
                                   side_effect=lambda path: next(hashes)), \
                 mock.patch.object(pack_export.modupdates, "version_by_hash",
                                   side_effect=[version, None]):
                result = pack_export.export(data, instance, root / "pack.mrpack",
                                            "1.20.1", "fabric")
            self.assertEqual(result["files"], 1)
            self.assertEqual(result["overrides"], 1)
            with zipfile.ZipFile(root / "pack.mrpack") as archive:
                names = archive.namelist()
                index = json.loads(archive.read("modrinth.index.json"))
            self.assertIn("overrides/mods/local.jar", names)
        self.assertEqual(index["dependencies"]["minecraft"], "1.20.1")
        self.assertEqual(index["files"][0]["downloads"], ["https://cdn.example/known.jar"])

    def test_network_error_moves_file_to_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "game"
            mods_dir = data / "instances" / "x" / "mods"
            mods_dir.mkdir(parents=True)
            (mods_dir / "a.jar").write_bytes(b"1")
            with mock.patch.object(pack_export.net, "sha1_file", return_value="aaa"), \
                 mock.patch.object(pack_export.modupdates, "version_by_hash",
                                   side_effect=modupdates.ModUpdateError("нет сети")):
                result = pack_export.export(data, "x", root / "p.mrpack")
        self.assertEqual(result["overrides"], 1)


class NewsTests(unittest.TestCase):
    def setUp(self) -> None:
        news.clear_cache()

    def test_news_parses_and_cleans(self) -> None:
        payload = {"entries": [
            {
                "id": "a",
                "title": "Halloween <b>on</b> Realms",
                "tag": "News",
                "category": "Minecraft: Java Edition",
                "date": "2026-10-30T10:00:00Z",
                "text": "12 new <em>maps</em>!",
                "readMoreLink": "https://www.minecraft.net/article/x",
                "newsPageImage": {"url": "/v2/images/a.png"},
            },
            {"title": "Bare", "date": "2026-10-01"},
            "garbage",
        ]}
        with mock.patch.object(news.net, "fetch_json", return_value=payload):
            rows = news.news(5)
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["title"], "Halloween on Realms")
        self.assertNotIn("<", first["title"])
        self.assertIn("12 new", first["text"])
        self.assertIn("maps", first["text"])
        self.assertNotIn("<em>", first["text"])
        self.assertEqual(first["date"], "2026-10-30")
        self.assertEqual(first["image"], news.BASE + "/v2/images/a.png")
        self.assertEqual(first["url"], "https://www.minecraft.net/article/x")
        self.assertEqual(rows[1]["tag"], "")
        self.assertEqual(rows[1]["image"], "")

    def test_news_respects_limit(self) -> None:
        payload = {"entries": [{"title": f"n{i}", "date": "2026-10-01"} for i in range(6)]}
        with mock.patch.object(news.net, "fetch_json", return_value=payload):
            self.assertEqual(len(news.news(2)), 2)

    def test_news_cached_between_calls(self) -> None:
        payload = {"entries": [{"title": "once", "date": "2026-10-01"}]}
        with mock.patch.object(news.net, "fetch_json", return_value=payload) as call:
            news.news(4)
            news.news(4)
        self.assertEqual(call.call_count, 1)
        news.clear_cache()
        with mock.patch.object(news.net, "fetch_json", return_value=payload) as call:
            news.news(4)
        self.assertEqual(call.call_count, 1)

    def test_news_network_error(self) -> None:
        with mock.patch.object(news.net, "fetch_json",
                               side_effect=net.DownloadError("boom")):
            with self.assertRaises(news.NewsError):
                news.news(4)

    def test_patch_notes_parse(self) -> None:
        payload = {"entries": [{
            "id": "26-4-snapshot-7",
            "title": "Minecraft 26.4 Snapshot 7",
            "version": "26.4-snapshot-7",
            "type": "snapshot",
            "date": "2026-11-03T13:08:56.000Z",
            "shortText": "<p>New music disc</p>",
            "image": {"url": "/v2/images/s.png"},
        }]}
        with mock.patch.object(news.net, "fetch_json", return_value=payload):
            rows = news.patch_notes(4)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["kind"], "patch")
        self.assertEqual(row["tag"], "snapshot")
        self.assertEqual(row["category"], "26.4-snapshot-7")
        self.assertEqual(row["text"], "New music disc")
        self.assertEqual(row["date"], "2026-11-03")
        self.assertEqual(row["url"], "")


class ModpackCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog.clear_cache()

    def test_search_packs_requests_modpack_type(self) -> None:
        with mock.patch.object(catalog, "search", return_value={}) as call:
            catalog.search_packs("q", "", "", "quests", "downloads", 1)
        args, kwargs = call.call_args
        self.assertEqual(kwargs.get("project_type"), "modpack")
        self.assertEqual(args[0], "q")
        self.assertEqual(args[3], "quests")
        self.assertEqual(args[5], "downloads")
        self.assertEqual(args[6], 1)

    def test_unique_instance_avoids_collision(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(modpack_source.loaders, "installed_versions",
                                   return_value=[{"id": "1.20.1-fabric"}]):
                name = modpack_source.unique_instance(Path(folder), "1.20.1", "fabric",
                                                      "sky pack")
        self.assertEqual(name, "1.20.1-fabric-sky-pack")

    def test_install_returns_new_instance(self) -> None:
        version = {"url": "https://cdn/p.mrpack", "filename": "p.mrpack", "size": 3}
        plan = {"game_version": "1.20.1", "loader": "fabric", "name": "Pack"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)

            def fake_download(url, target, *args, **kwargs):
                Path(target).write_bytes(b"zip")

            class FakeInstaller:
                def __init__(self, *args, **kwargs):
                    pass

                def install(self, game, loader):
                    return {"version_id": f"{game}-{loader}"}

            with mock.patch.object(modpack_source.net, "download",
                                   side_effect=fake_download), \
                 mock.patch.object(modpack_source.modpacks, "inspect",
                                   return_value=plan), \
                 mock.patch.object(modpack_source.modpacks, "install",
                                   return_value={"installed": 1}), \
                 mock.patch.object(modpack_source.installer_core, "Installer",
                                   FakeInstaller):
                result = modpack_source.install(version, root / "game")
        self.assertEqual(result["instance"], "1.20.1-fabric")
        self.assertEqual(result["pack_name"], "Pack")
        self.assertEqual(result["installed"], 1)


class FsUtilTests(unittest.TestCase):
    def test_atomic_write_replaces_without_leftovers(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "cfg.json"
            fsutil.atomic_write_text(target, "first")
            fsutil.atomic_write_text(target, "second")
            self.assertEqual(target.read_text(encoding="utf-8"), "second")
            leftovers = [p.name for p in Path(folder).iterdir() if p.name != "cfg.json"]
            self.assertEqual(leftovers, [])

    def test_transaction_commit_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder)
            (dest / "a.jar").write_bytes(b"old")
            transaction = fsutil.FolderTransaction(dest)
            staged = transaction.stage("a.jar")
            staged.write_bytes(b"new")
            transaction.commit()
            self.assertEqual((dest / "a.jar").read_bytes(), b"new")
            transaction.rollback()
            self.assertEqual((dest / "a.jar").read_bytes(), b"old")
            transaction.close()
            leftovers = [p for p in dest.iterdir() if p.name.startswith(".vleyz-tx")]
            self.assertEqual(leftovers, [])


class TransactionRollbackTests(unittest.TestCase):
    def test_failed_install_keeps_old_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder)
            (dest / "a.jar").write_bytes(b"old")
            files = [{"title": "A", "filename": "a.jar", "url": "https://example/a.jar",
                      "size": 5, "sha1": None}]

            def failing_download(url, target, *args, **kwargs):
                Path(target).write_bytes(b"par")
                raise net.DownloadError("обрыв")

            with mock.patch.object(modrinth.net, "download",
                                   side_effect=failing_download):
                with self.assertRaises(net.DownloadError):
                    modrinth.install(files, dest)
            self.assertEqual((dest / "a.jar").read_bytes(), b"old")
            leftovers = [p for p in dest.iterdir() if p.name.startswith(".vleyz-tx")]
            self.assertEqual(leftovers, [])


class ConfigCheckTests(unittest.TestCase):
    def test_validate_repairs_garbage(self) -> None:
        cfg = {
            "nickname": "  bad\x00name  ",
            "ram_mb": 999999,
            "theme": "neon",
            "language": "zz",
            "favorites": ["a", "a", "", 5],
            "servers": [{"name": "", "address": " play.example "}, "junk"],
            "profiles": [{"id": "", "name": "", "nickname": "x" * 40}],
            "active_profile": "missing",
            "gigachat": {"enabled": "yes", "model": ""},
        }
        clean, notes = config_check.validate(cfg)
        self.assertEqual(clean["nickname"], "badname")
        self.assertEqual(clean["ram_mb"], config_check.RAM_MAX)
        self.assertEqual(clean["theme"], "dark")
        self.assertEqual(clean["language"], "ru")
        self.assertEqual(clean["favorites"], ["a"])
        self.assertEqual(clean["servers"][0]["address"], "play.example")
        self.assertEqual(clean["servers"][0]["name"], "play.example")
        self.assertEqual(clean["active_profile"], "")
        self.assertEqual(clean["gigachat"]["model"], "GigaChat")
        self.assertTrue(clean["gigachat"]["enabled"])
        self.assertTrue(notes)

    def test_valid_config_is_untouched(self) -> None:
        cfg = {
            "nickname": "Steve",
            "ram_mb": 2048,
            "theme": "dark",
            "language": "ru",
            "favorites": [],
            "servers": [],
            "profiles": [],
            "active_profile": "",
            "gigachat": {"client_id": "", "client_secret": "",
                         "model": "GigaChat", "enabled": False},
        }
        _clean, notes = config_check.validate(cfg)
        self.assertEqual(notes, [])


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False

    def read(self) -> bytes:
        return self.payload


class NetRetryTests(unittest.TestCase):
    def test_delay_bounds_and_retry_after(self) -> None:
        for attempt in range(10):
            self.assertGreaterEqual(net._retry_delay(attempt), 0.5)
            self.assertLessEqual(net._retry_delay(attempt), net.RETRY_MAX)
        error = urllib.error.HTTPError("u", 429, "slow", {"Retry-After": "2"}, None)
        self.assertEqual(net._retry_delay(0, error), 2.0)
        self.assertFalse(net._retryable(urllib.error.HTTPError("u", 404, "nf", {}, None)))
        self.assertTrue(net._retryable(urllib.error.URLError("boom")))

    def test_fetch_json_retries_then_succeeds(self) -> None:
        calls = {"count": 0}

        def fake_urlopen(request, timeout=0, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise urllib.error.URLError("boom")
            return FakeResponse(b'{"ok": 1}')

        with mock.patch.object(net.urllib.request, "urlopen",
                               side_effect=fake_urlopen), \
             mock.patch.object(net.time, "sleep"):
            data = net.fetch_json("https://example/api", retries=3)
        self.assertEqual(data, {"ok": 1})
        self.assertEqual(calls["count"], 2)

    def test_not_found_is_not_retried(self) -> None:
        error = urllib.error.HTTPError("u", 404, "nf", {}, None)
        with mock.patch.object(net.urllib.request, "urlopen",
                               side_effect=error) as opener, \
             mock.patch.object(net.time, "sleep"):
            with self.assertRaises(net.DownloadError):
                net.fetch_json("https://example/api", retries=3)
        self.assertEqual(opener.call_count, 1)


class ModMetaTests(unittest.TestCase):
    def _jar(self, folder: Path, name: str, payload: dict) -> None:
        with zipfile.ZipFile(folder / name, "w") as archive:
            archive.writestr("fabric.mod.json", json.dumps(payload))

    def test_scan_duplicates_and_missing_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._jar(root, "a.jar", {"id": "sodium", "name": "Sodium", "version": "1",
                                      "depends": {"minecraft": "*", "cloth": "*"}})
            self._jar(root, "b.jar", {"id": "sodium", "name": "Sodium dup", "version": "2"})
            self._jar(root, "c.jar", {"id": "iris", "name": "Iris", "version": "1",
                                      "depends": {"sodium": "*", "missinglib": "*"}})
            (root / "broken.jar").write_bytes(b"not a zip")
            rows = modmeta.scan(root)
            duplicates = modmeta.duplicates(rows)
            missing = modmeta.missing_dependencies(rows)
        self.assertEqual(len(rows), 3)
        self.assertEqual(duplicates[0]["id"], "sodium")
        self.assertEqual(sorted(duplicates[0]["files"]), ["a.jar", "b.jar"])
        deps = {item["dep"] for item in missing}
        self.assertEqual(deps, {"cloth", "missinglib"})

    def test_scan_missing_folder(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(modmeta.scan(Path(folder) / "none"), [])

    def test_toml_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "forge.jar"
            toml = ('modLoader="javafml"\n[[mods]]\n'
                    'modId="examplemod"\ndisplayName="Example"\nversion="1.2.3"\n')
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("META-INF/mods.toml", toml)
            record = modmeta.read(target)
        self.assertEqual(record["id"], "examplemod")
        self.assertEqual(record["name"], "Example")
        self.assertEqual(record["version"], "1.2.3")


class EventBusTests(unittest.TestCase):
    def test_subscribe_emit_unsubscribe(self) -> None:
        bus = events.EventBus()
        seen: list[int] = []
        unsubscribe = bus.subscribe("x", lambda value: seen.append(value))
        self.assertEqual(bus.emit("x", value=1), 1)
        unsubscribe()
        self.assertEqual(bus.emit("x", value=2), 0)
        self.assertEqual(seen, [1])

    def test_broken_handler_does_not_break_others(self) -> None:
        bus = events.EventBus()
        seen: list[int] = []

        def broken(value):
            raise RuntimeError("boom")

        bus.subscribe("y", broken)
        bus.subscribe("y", lambda value: seen.append(value))
        self.assertEqual(bus.emit("y", value=7), 2)
        self.assertEqual(seen, [7])


class JobRunnerTests(unittest.TestCase):
    def test_result_and_duplicate_protection(self) -> None:
        runner = jobs.JobRunner(workers=2)
        started = threading.Event()
        release = threading.Event()

        def slow():
            started.set()
            release.wait(2)
            return "ok"

        self.assertTrue(runner.submit("slow", slow))
        self.assertTrue(started.wait(1))
        self.assertFalse(runner.submit("slow", slow))
        release.set()
        data = runner.wait("slow", 2)
        self.assertEqual(data["result"], "ok")
        self.assertIsNone(data["error"])

    def test_error_is_captured(self) -> None:
        runner = jobs.JobRunner(workers=1)

        def boom():
            raise ValueError("bad")

        self.assertTrue(runner.submit("boom", boom))
        data = runner.wait("boom", 2)
        self.assertIsInstance(data["error"], ValueError)


class InstallerJarTests(unittest.TestCase):
    def _plan(self) -> dict:
        return {
            "installer_url": "https://example.invalid/forge-installer.jar",
            "label": "Forge 1.0",
            "game": "1.20.1",
            "loader": "forge",
        }

    def test_broken_download_raises(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            runner = installer.Installer(data, installer.Progress())

            def fake_download(url, dest, **kwargs):
                Path(dest).parent.mkdir(parents=True, exist_ok=True)
                Path(dest).write_bytes(b"not a zip")

            with mock.patch.object(installer.net, "download",
                                   side_effect=fake_download) as call:
                with self.assertRaises(installer.InstallError):
                    runner._run_installer(self._plan(), 17)
        self.assertEqual(call.call_count, 1)

    def test_corrupt_jar_on_disk_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            jar = data / "installers" / "forge-installer.jar"
            jar.parent.mkdir(parents=True)
            jar.write_bytes(b"old garbage")
            runner = installer.Installer(data, installer.Progress())

            def fake_download(url, dest, **kwargs):
                Path(dest).write_bytes(b"still not a zip")

            with mock.patch.object(installer.net, "download",
                                   side_effect=fake_download) as call:
                with self.assertRaises(installer.InstallError):
                    runner._run_installer(self._plan(), 17)
            self.assertEqual(call.call_count, 1)
            self.assertFalse(zipfile.is_zipfile(jar))

    def test_valid_jar_skips_download(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            jar = data / "installers" / "forge-installer.jar"
            jar.parent.mkdir(parents=True)
            with zipfile.ZipFile(jar, "w") as archive:
                archive.writestr("install_profile.json", "{}")
            runner = installer.Installer(data, installer.Progress())
            finished = installer.subprocess.CompletedProcess(
                args=[], returncode=0, stdout="ok", stderr=""
            )
            with mock.patch.object(installer.net, "download") as download, \
                 mock.patch.object(installer.java_core, "ensure_java",
                                   return_value=(Path("javaw.exe"), 17)), \
                 mock.patch.object(installer.subprocess, "run", return_value=finished), \
                 mock.patch.object(installer.loaders, "find_installed",
                                   return_value="1.20.1-forge-1.0"):
                installed = runner._run_installer(self._plan(), 17)
        self.assertEqual(installed, "1.20.1-forge-1.0")
        download.assert_not_called()


class ModpackInstanceTests(unittest.TestCase):
    def test_pack_installs_version_and_uses_it_as_instance(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            version = {"url": "https://example.invalid/pack.mrpack",
                       "filename": "pack.mrpack", "name": "Test Pack"}
            plan = {"game_version": "1.20.1", "loader": "forge", "name": "Test Pack"}
            calls: dict[str, object] = {}

            class FakeInstaller:
                def __init__(self, *args, **kwargs):
                    pass

                def install(self, game, loader):
                    calls["version"] = (game, loader)
                    return {"version_id": "1.20.1-forge-47.4.28"}

            def fake_install(data_dir, instance_id, archive, log_fn=None, cancel=None):
                calls["instance"] = instance_id
                return {"instance": instance_id}

            with mock.patch.object(modpack_source.net, "download"), \
                 mock.patch.object(modpack_source.modpacks, "inspect",
                                   return_value=plan), \
                 mock.patch.object(modpack_source.modpacks, "install",
                                   side_effect=fake_install), \
                 mock.patch.object(modpack_source.installer_core, "Installer",
                                   FakeInstaller):
                result = modpack_source.install(version, data)

        self.assertEqual(calls["version"], ("1.20.1", "forge"))
        self.assertEqual(calls["instance"], "1.20.1-forge-47.4.28")
        self.assertEqual(result["instance"], "1.20.1-forge-47.4.28")
        self.assertEqual(result["loader"], "forge")

    def test_pack_without_loader_falls_back_to_instance(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            version = {"url": "https://example.invalid/pack.mrpack",
                       "filename": "pack.mrpack"}
            plan = {"game_version": "", "loader": "", "name": "Zip Pack"}
            calls: dict[str, object] = {}

            def fake_install(data_dir, instance_id, archive, log_fn=None, cancel=None):
                calls["instance"] = instance_id
                return {"instance": instance_id}

            with mock.patch.object(modpack_source.net, "download"), \
                 mock.patch.object(modpack_source.modpacks, "inspect",
                                   return_value=plan), \
                 mock.patch.object(modpack_source.modpacks, "install",
                                   side_effect=fake_install):
                result = modpack_source.install(version, data)

        self.assertEqual(calls["instance"], "pack")
        self.assertEqual(result["instance"], "pack")


class CleanupTests(unittest.TestCase):
    def _make_tree(self, root: Path) -> None:
        (root / "installers").mkdir(parents=True)
        (root / "installers" / "forge.jar").write_bytes(b"x" * 100)
        (root / "cache").mkdir()
        (root / "cache" / "modrinth.json").write_bytes(b"y" * 50)
        (root / "mods").mkdir()
        (root / "mods" / "keep.jar").write_bytes(b"z" * 10)
        (root / "mods" / "half.part").write_bytes(b"p" * 5)
        (root / "downloads").mkdir()
        (root / "downloads" / "lib.part").write_bytes(b"q" * 7)
        (root / "logs").mkdir()
        for index in range(25):
            item = root / "logs" / f"log{index:02d}.log"
            item.write_bytes(b"l")
            os.utime(item, (index, index))

    def test_scan_counts_safe_categories(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._make_tree(root)
            rows = {row["key"]: row for row in cleanup.scan(root)}
        self.assertEqual(rows["installers"]["files"], 1)
        self.assertEqual(rows["installers"]["size"], 100)
        self.assertEqual(rows["partial"]["files"], 1)
        self.assertEqual(rows["partial"]["size"], 7)
        self.assertEqual(rows["cache"]["files"], 1)
        self.assertEqual(rows["logs"]["files"], 5)

    def test_clean_keeps_player_data(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._make_tree(root)
            result = cleanup.clean(root, ["installers", "partial"])
            self.assertEqual(result["removed"], 2)
            self.assertEqual(result["freed"], 107)
            self.assertFalse((root / "installers" / "forge.jar").exists())
            self.assertFalse((root / "downloads" / "lib.part").exists())
            self.assertTrue((root / "mods" / "keep.jar").exists())
            self.assertTrue((root / "mods" / "half.part").exists())
            self.assertTrue((root / "cache" / "modrinth.json").exists())

    def test_unknown_category_raises(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(cleanup.CleanupError):
                cleanup.clean(Path(folder), ["saves"])


class VersionManifestTests(unittest.TestCase):
    def _cache(self, root: Path, age: float) -> Path:
        folder = root / "versions"
        folder.mkdir(parents=True)
        cache = folder / "version_manifest_v2.json"
        cache.write_text(json.dumps({"versions": [{"id": "1.20.1"}]}), encoding="utf-8")
        stamp = time.time() - age
        os.utime(cache, (stamp, stamp))
        return cache

    def test_fresh_cache_skips_network(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._cache(root, 10)
            with mock.patch.object(versions.net, "fetch_json") as call:
                data = versions.manifest(root)
        self.assertEqual(data["versions"][0]["id"], "1.20.1")
        call.assert_not_called()

    def test_stale_cache_used_when_network_fails(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._cache(root, versions.MANIFEST_TTL + 60)
            with mock.patch.object(versions.net, "fetch_json",
                                   side_effect=versions.net.DownloadError("нет сети")):
                data = versions.manifest(root)
        self.assertEqual(data["versions"][0]["id"], "1.20.1")

    def test_no_cache_and_no_network_raises(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(versions.net, "fetch_json",
                                   side_effect=versions.net.DownloadError("нет сети")):
                with self.assertRaises(versions.net.DownloadError):
                    versions.manifest(Path(folder))


class InstalledPacksTests(unittest.TestCase):
    def _pack(self, data: Path, instance_id: str, payload: dict) -> Path:
        folder = data / "instances" / instance_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / modpacks.META_NAME).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return folder

    def test_lists_only_packs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            self._pack(data, "1.20.1-forge-47.4.28", {
                "name": "Hi Verity!", "version": "1.0.0",
                "game_version": "1.20.1", "loader": "forge",
            })
            (data / "instances" / "plain").mkdir(parents=True)
            rows = modpacks.installed_packs_all(data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Hi Verity!")
        self.assertEqual(rows[0]["version"], "1.0.0")
        self.assertEqual(rows[0]["instance"], "1.20.1-forge-47.4.28")

    def test_pack_title_for_library(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            self._pack(data, "pack-1", {"name": "Hi Verity!", "version": "1.0.0"})
            self.assertEqual(modpacks.pack_title(data, "pack-1"), "Hi Verity! 1.0.0")
            self.assertEqual(modpacks.pack_title(data, "plain"), "")

    def test_broken_meta_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            folder_path = data / "instances" / "broken"
            folder_path.mkdir(parents=True)
            (folder_path / modpacks.META_NAME).write_text("{не json", encoding="utf-8")
            self.assertEqual(modpacks.installed_packs_all(data), [])


@unittest.skipUnless(sys.platform == "win32", "связка папок проверяется на Windows")
class SharedModsTests(unittest.TestCase):
    def test_moves_mods_to_root_and_links_instance(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            instance = "1.20.1-forge-1"
            enabled = data / "instances" / instance / "mods"
            enabled.mkdir(parents=True)
            (enabled / "a.jar").write_bytes(b"x")
            disabled = data / "instances" / instance / "mods-disabled"
            disabled.mkdir(parents=True)
            (disabled / "b.jar").write_bytes(b"y")

            mods.ensure_shared(data, instance)

            self.assertTrue((data / "mods" / "a.jar").is_file())
            self.assertTrue((data / "mods-disabled" / "b.jar").is_file())
            self.assertEqual(len(mods.list_mods(data, instance)), 2)
            self.assertEqual(len(mods.list_mods(data, None)), 2)

            self.assertTrue(mods.set_enabled(data, "a.jar", False, instance))
            self.assertTrue((data / "mods-disabled" / "a.jar").is_file())
            self.assertEqual(
                [item["name"] for item in mods.list_mods(data, instance) if item["enabled"]],
                [],
            )

    def test_ensure_shared_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            instance = "pack"
            (data / "instances" / instance / "mods").mkdir(parents=True)
            mods.ensure_shared(data, instance)
            mods.ensure_shared(data, instance)
            self.assertTrue((data / "instances" / instance / "mods").is_dir())


class PackToggleTests(unittest.TestCase):
    def _pack(self, data: Path, instance: str, files: list[str],
              enabled: bool = True) -> None:
        folder = data / "instances" / instance
        folder.mkdir(parents=True, exist_ok=True)
        (folder / modpacks.META_NAME).write_text(json.dumps({
            "name": "Pack", "version": "1.0", "game_version": "1.20.1",
            "loader": "forge", "files": files, "enabled": enabled,
        }), encoding="utf-8")
        (data / "mods").mkdir(exist_ok=True)

    def test_disable_and_enable_moves_only_pack_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            self._pack(data, "pack", ["a.jar", "b.jar"])
            (data / "mods" / "a.jar").write_bytes(b"a")
            (data / "mods" / "b.jar").write_bytes(b"b")
            (data / "mods" / "own.jar").write_bytes(b"o")

            result = modpacks.set_pack_enabled(data, "pack", False)
            self.assertEqual(result["moved"], 2)
            self.assertFalse((data / "mods" / "a.jar").exists())
            self.assertTrue((data / "mods-disabled" / "a.jar").is_file())
            self.assertTrue((data / "mods" / "own.jar").is_file())
            self.assertFalse(modpacks.installed_packs_all(data)[0]["enabled"])

            modpacks.set_pack_enabled(data, "pack", True)
            self.assertTrue((data / "mods" / "a.jar").is_file())
            self.assertTrue(modpacks.installed_packs_all(data)[0]["enabled"])

    def test_pack_owners_maps_only_pack_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            self._pack(data, "pack", ["a.jar"])
            owners = modpacks.pack_owners(data)
        self.assertEqual(owners.get("a.jar"), "Pack")
        self.assertNotIn("own.jar", owners)


class JavaRequirementTests(unittest.TestCase):
    @staticmethod
    def _write(root: Path, version_id: str, payload: dict) -> None:
        folder = root / "versions" / version_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{version_id}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def test_loader_inherits_java_from_parent(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            self._write(data, "1.20.1", {"id": "1.20.1", "javaVersion": {"majorVersion": 17}})
            self._write(data, "1.20.1-forge-47.4.28",
                        {"id": "1.20.1-forge-47.4.28", "inheritsFrom": "1.20.1"})
            self.assertEqual(versions.java_requirement(data, "1.20.1-forge-47.4.28"), 17)

    def test_chain_of_two_parents(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            self._write(data, "1.20.1", {"id": "1.20.1", "javaVersion": {"majorVersion": 17}})
            self._write(data, "mid", {"id": "mid", "inheritsFrom": "1.20.1"})
            self._write(data, "top", {"id": "top", "inheritsFrom": "mid"})
            self.assertEqual(versions.java_requirement(data, "top"), 17)

    def test_unknown_version_falls_back_to_eight(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            (data / "versions").mkdir()
            with mock.patch.object(versions, "version_meta",
                                   side_effect=versions.VersionError("нет")):
                self.assertEqual(versions.java_requirement(data, "нет-такой"), 8)


class JavaOsSupportTests(unittest.TestCase):
    @staticmethod
    def _version(major: int, minor: int, build: int = 0):
        return mock.Mock(major=major, minor=minor, build=build)

    def test_windows7_limits_java_to_eight(self) -> None:
        with mock.patch.object(java.sys, "getwindowsversion",
                               return_value=self._version(6, 1, 7601)):
            self.assertTrue(java.is_windows7())
            self.assertEqual(java.max_java_major(), 8)
            java.check_java_supported(8)
            with self.assertRaises(net.DownloadError):
                java.check_java_supported(17)

    def test_windows10_allows_java17(self) -> None:
        with mock.patch.object(java.sys, "getwindowsversion",
                               return_value=self._version(10, 0, 19045)):
            self.assertFalse(java.is_windows7())
            self.assertEqual(java.max_java_major(), java.JAVA_MAX)
            java.check_java_supported(17)

    def test_windows8_still_allows_java17(self) -> None:
        with mock.patch.object(java.sys, "getwindowsversion",
                               return_value=self._version(6, 3, 9600)):
            self.assertFalse(java.is_windows7())
            java.check_java_supported(17)

    def test_provision_refuses_java17_on_windows7(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(java.sys, "getwindowsversion",
                                   return_value=self._version(6, 1, 7601)), \
                    mock.patch.object(java.paths, "java_root", return_value=Path(folder)):
                with self.assertRaises(net.DownloadError):
                    java.provision(17)


def _skin_png(width: int, height: int) -> bytes:
    """Настоящий PNG нужного размера — для проверок без картинок на диске."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + b"\x00" * 4 * width for _ in range(height))
    return (skins.PNG_SIGNATURE
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


class SkinTests(unittest.TestCase):
    def test_skin_from_file_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "skin.png"
            source.write_bytes(_skin_png(64, 64))
            saved = skins.from_file(source, Path(folder))
            self.assertEqual(skins.validate_skin(saved), (64, 64))
            self.assertEqual(skins.png_size(saved), (64, 64))

    def test_old_64x32_skin_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "old.png"
            source.write_bytes(_skin_png(64, 32))
            self.assertEqual(skins.from_file(source, Path(folder)).suffix, ".png")

    def test_wrong_size_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "big.png"
            source.write_bytes(_skin_png(128, 128))
            with self.assertRaises(skins.SkinError):
                skins.from_file(source, Path(folder))

    def test_not_png_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "x.png"
            source.write_bytes(b"<html>not a skin</html>")
            with self.assertRaises(skins.SkinError):
                skins.from_file(source, Path(folder))

    def test_url_must_be_http(self) -> None:
        for bad in ("", "file:///C:/skin.png", "ftp://site/skin.png", "site/skin.png"):
            with self.assertRaises(skins.SkinError):
                skins.from_url(Path("."), bad)

    def test_local_network_url_rejected(self) -> None:
        for bad in ("http://127.0.0.1/skin.png", "http://localhost/skin.png"):
            with self.assertRaises(skins.SkinError):
                skins.from_url(Path("."), bad)

    def test_upload_needs_token_and_valid_skin(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "skin.png"
            source.write_bytes(_skin_png(64, 64))
            with self.assertRaises(skins.SkinError):
                skins.upload("", source)


class ServersDatTests(unittest.TestCase):
    def test_written_file_is_uncompressed_nbt(self) -> None:
        import gzip

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            cfg = {"servers": [{"id": "a", "name": "Тест", "address": "play.example.com"}]}
            servers.sync_servers_dat(target, cfg)
            data = (target / "servers.dat").read_bytes()
            self.assertEqual(data[:1], b"\x0a")
            self.assertNotEqual(data[:2], b"\x1f\x8b")
            self.assertEqual(
                servers._read_servers_dat(target / "servers.dat"),
                [{"name": "Тест", "ip": "play.example.com"}],
            )
            with self.assertRaises(Exception):
                gzip.decompress(data)

    def test_server_added_in_game_survives_sync(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            servers.sync_servers_dat(target, {"servers": []})
            path = target / "servers.dat"
            servers._write_servers_dat(path, [{"name": "Из игры", "ip": "mc.example.com"}])
            before = path.read_bytes()

            servers.sync_servers_dat(target, {"servers": []})

            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(servers._read_servers_dat(path)[0]["name"], "Из игры")

    def test_launcher_and_game_lists_merge(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            path = target / "servers.dat"
            servers._write_servers_dat(path, [{"name": "Из игры", "ip": "mc.example.com"}])
            servers.sync_servers_dat(
                target, {"servers": [{"id": "b", "name": "Из лаунчера", "address": "play.net"}]}
            )
            names = [item["name"] for item in servers._read_servers_dat(path)]
            self.assertEqual(names, ["Из лаунчера", "Из игры"])

    def test_gzip_file_from_old_build_still_readable(self) -> None:
        import gzip

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            path = target / "servers.dat"
            servers._write_servers_dat(path, [{"name": "Старый", "ip": "1.2.3.4"}])
            path.write_bytes(gzip.compress(path.read_bytes()))
            self.assertEqual(servers._read_servers_dat(path)[0]["ip"], "1.2.3.4")


class TelemetryTests(unittest.TestCase):
    def test_ping_sent_once_per_install(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            with mock.patch.object(telemetry.net, "fetch_text", return_value="{}") as fetch:
                self.assertTrue(telemetry.ping_install({"telemetry": True}, base))
                self.assertFalse(telemetry.ping_install({"telemetry": True}, base))
            self.assertEqual(fetch.call_count, 1)
            self.assertTrue((base / telemetry.MARKER).is_file())

    def test_disabled_in_config_sends_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            with mock.patch.object(telemetry.net, "fetch_text") as fetch:
                self.assertFalse(telemetry.ping_install({"telemetry": False}, base))
            fetch.assert_not_called()
            self.assertFalse((base / telemetry.MARKER).exists())

    def test_network_error_keeps_marker_absent(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            with mock.patch.object(telemetry.net, "fetch_text",
                                   side_effect=net.DownloadError("нет сети")):
                self.assertFalse(telemetry.ping_install({}, base))
            self.assertFalse((base / telemetry.MARKER).exists())

    def test_async_ping_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            with mock.patch.object(telemetry.net, "fetch_text", return_value="{}"):
                thread = telemetry.ping_install_async({"telemetry": True}, base)
                thread.join(timeout=5)
            self.assertTrue((base / telemetry.MARKER).is_file())


class CrashDiagnoseTests(unittest.TestCase):
    def test_incompatible_mod_model(self) -> None:
        text = (
            "Description: Rendering overlay\n"
            "java.lang.IllegalArgumentException: Failed to create model for luckytowers:lucky_bot\n"
            "Caused by: java.util.NoSuchElementException: Can't find part ear\n"
        )
        result = crash.analyze(text)
        self.assertEqual(result["mod"], "luckytowers")
        self.assertIn("luckytowers", result["cause"])
        self.assertIn("ear", result["cause"])
        self.assertTrue(result["advice"])

    def test_out_of_memory(self) -> None:
        result = crash.analyze("Description: Ticking\njava.lang.OutOfMemoryError: Java heap space")
        self.assertIn("памяти", result["cause"])
        self.assertIn("4096", result["advice"])

    def test_missing_dependencies(self) -> None:
        text = (
            "Description: Mod loading\n"
            "Missing or unsupported mandatory dependencies:\n"
            "\tMod ID: 'geckolib', Requested by: 'verity'\n"
        )
        result = crash.analyze(text)
        self.assertIn("зависимост", result["cause"])
        self.assertEqual(result["mod"], "geckolib")

    def test_duplicate_mods(self) -> None:
        result = crash.analyze("Description: Loading\nFound duplicate mods:\n\tmod.jar")
        self.assertIn("два одинаковых", result["cause"])

    def test_unknown_crash_has_no_advice(self) -> None:
        result = crash.analyze("Description: Something\nsome random stack")
        self.assertEqual(result["cause"], "")
        self.assertEqual(result["advice"], "")


class ModVersionRangeTests(unittest.TestCase):
    def test_range_bounds(self) -> None:
        self.assertTrue(modmeta.range_accepts("[1.20,1.21)", "1.20.1"))
        self.assertTrue(modmeta.range_accepts("[1.20.1,1.20.2]", "1.20.1"))
        self.assertFalse(modmeta.range_accepts("[1.21,1.22)", "1.20.1"))
        self.assertFalse(modmeta.range_accepts("(1.20.1,1.21)", "1.20.1"))
        self.assertTrue(modmeta.range_accepts("*", "1.20.1"))

    def test_unknown_format_is_not_a_problem(self) -> None:
        self.assertTrue(modmeta.range_accepts("1.20.*", "1.20.1"))
        self.assertTrue(modmeta.range_accepts("", "1.20.1"))

    def test_mismatch_finds_foreign_mod(self) -> None:
        records = [
            {"id": "good", "name": "Good", "file": "good.jar", "minecraft_range": "[1.20,1.21)"},
            {"id": "bad", "name": "Bad", "file": "bad.jar", "minecraft_range": "[1.21,1.22)"},
        ]
        found = modmeta.minecraft_mismatch(records, "1.20.1")
        self.assertEqual([item["id"] for item in found], ["bad"])


if __name__ == "__main__":
    unittest.main()