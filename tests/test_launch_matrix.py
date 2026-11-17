"""Матрица команды запуска: разные поколения версий и наследование.

Проверяем без сети: version json лежат в временной папке, поэтому тест
ловит поломки сборки аргументов и автоподключения к серверу.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vleyzlauncher.core import launch  # noqa: E402


def _write(root: Path, version_id: str, data: dict) -> None:
    folder = root / "versions" / version_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{version_id}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


MODERN = {
    "id": "1.20.1",
    "type": "release",
    "mainClass": "net.minecraft.client.main.Main",
    "assets": "5",
    "assetIndex": {"id": "5"},
    "downloads": {"client": {"url": "https://example.invalid/client.jar", "sha1": "00", "size": 1}},
    "libraries": [],
    "arguments": {
        "game": [
            "--username", "${auth_player_name}",
            "--version", "${version_name}",
            "--gameDir", "${game_directory}",
            "--assetsDir", "${assets_root}",
            "--assetIndex", "${assets_index_name}",
            "--uuid", "${auth_uuid}",
            "--accessToken", "${auth_access_token}",
            "--userType", "${user_type}",
        ],
        "jvm": ["-Djava.library.path=${natives_directory}", "-cp", "${classpath}"],
    },
}

LEGACY = {
    "id": "1.12.2",
    "type": "release",
    "mainClass": "net.minecraft.client.main.Main",
    "minecraftArguments": (
        "--username ${auth_player_name} --version ${version_name} "
        "--gameDir ${game_directory} --assetsDir ${assets_root} "
        "--assetIndex ${assets_index_name} --uuid ${auth_uuid} "
        "--accessToken ${auth_access_token} --userType ${user_type}"
    ),
    "assetIndex": {"id": "1.12"},
    "downloads": {"client": {"url": "https://example.invalid/client.jar", "sha1": "00", "size": 1}},
    "libraries": [],
}

SNAPSHOT = {
    "id": "26.4-snapshot",
    "type": "snapshot",
    "mainClass": "net.minecraft.client.main.Main",
    "assetIndex": {"id": "26.4"},
    "downloads": {"client": {"url": "https://example.invalid/client.jar", "sha1": "00", "size": 1}},
    "libraries": [],
    "arguments": {
        "game": ["--username", "${auth_player_name}"],
        "jvm": ["-cp", "${classpath}"],
    },
}

CHILD = {
    "id": "1.20.1-forge-47.4.28",
    "inheritsFrom": "1.20.1",
    "type": "release",
    "mainClass": "cpw.mods.bootstraplauncher.BootstrapLauncher",
    "libraries": [{
        "name": "net.minecraftforge:forge:47.4.28",
        "downloads": {"artifact": {
            "path": "net/minecraftforge/forge/47.4.28/forge-47.4.28.jar",
        }},
    }],
}


class LaunchMatrixTests(unittest.TestCase):
    def _command(self, version_id: str, server=None, extra=()) -> list[str]:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for version_id_data in (MODERN, LEGACY, SNAPSHOT):
                _write(root, version_id_data["id"], version_id_data)
            _write(root, CHILD["id"], CHILD)
            return launch.build_command(
                root, version_id, "TestPlayer", 2048, Path("javaw.exe"),
                server=server, jvm_args="",
            )

    def test_modern_uses_quick_play(self) -> None:
        command = self._command("1.20.1", server=("mc.example", 25565))
        self.assertIn("--quickPlayMultiplayer", command)
        self.assertIn("mc.example:25565", command)
        self.assertNotIn("${", " ".join(command))
        self.assertIn("net.minecraft.client.main.Main", command)

    def test_legacy_uses_server_flags(self) -> None:
        command = self._command("1.12.2", server=("mc.example", 25566))
        self.assertNotIn("--quickPlayMultiplayer", command)
        self.assertEqual(command[command.index("--server") + 1], "mc.example")
        self.assertEqual(command[command.index("--port") + 1], "25566")
        self.assertNotIn("${", " ".join(command))

    def test_snapshot_counts_as_modern(self) -> None:
        command = self._command("26.4-snapshot", server=("mc.example", 25565))
        self.assertIn("--quickPlayMultiplayer", command)

    def test_child_keeps_own_main_class_and_parent_jar(self) -> None:
        command = self._command("1.20.1-forge-47.4.28")
        self.assertIn("cpw.mods.bootstraplauncher.BootstrapLauncher", command)
        self.assertNotIn("net.minecraft.client.main.Main", command)
        classpath = command[command.index("-cp") + 1]
        self.assertIn("versions", classpath)
        self.assertIn("1.20.1", classpath)
        self.assertIn("forge-47.4.28.jar", classpath)

    def test_no_server_means_no_quick_play(self) -> None:
        command = self._command("1.20.1")
        self.assertNotIn("--quickPlayMultiplayer", command)
        self.assertNotIn("--server", command)

    def test_offline_uuid_is_stable(self) -> None:
        self.assertEqual(launch.offline_uuid("TestPlayer"),
                         launch.offline_uuid("TestPlayer"))
        self.assertNotEqual(launch.offline_uuid("TestPlayer"),
                            launch.offline_uuid("Other"))


if __name__ == "__main__":
    unittest.main()
