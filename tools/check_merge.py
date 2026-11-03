"""Проверка слияния inheritsFrom на синтетических данных (без сети)."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vleyzlauncher.core import versions

PARENT = {
    "id": "1.20.1",
    "mainClass": "net.minecraft.client.main.Main",
    "type": "release",
    "assets": "5",
    "assetIndex": {"id": "5", "url": "http://example/5.json"},
    "downloads": {"client": {"url": "http://example/client.jar", "size": 100}},
    "javaVersion": {"majorVersion": 17},
    "libraries": [
        {"name": "org.ow2.asm:asm:9.5", "downloads": {"artifact": {"path": "org/ow2/asm/asm/9.5/asm-9.5.jar",
                                                                 "url": "http://example/asm.jar"}}},
        {"name": "com.mojang:brigadier:1.0.18", "downloads": {"artifact": {
            "path": "com/mojang/brigadier/1.0.18/brigadier-1.0.18.jar", "url": "http://example/brigadier.jar"}}},
    ],
    "arguments": {
        "jvm": ["-Djava.library.path=${natives_directory}", "-cp", "${classpath}"],
        "game": ["--username", "${auth_player_name}"],
    },
}

CHILD = {
    "id": "1.20.1-fabric",
    "inheritsFrom": "1.20.1",
    "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
    "libraries": [
        {"name": "org.ow2.asm:asm:9.7.1", "url": "https://maven.fabricmc.net/",
         "sha1": "f0ed132a49244b042cd0e15702ab9f2ce3cc8436", "size": 126093},
        {"name": "net.fabricmc:fabric-loader:0.16.9", "url": "https://maven.fabricmc.net/",
         "sha1": "abc", "size": 1000},
    ],
    "arguments": {"jvm": ["-DFabricMcEmu= net.minecraft.client.main.Main "], "game": []},
}


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        for name, payload in (("1.20.1", PARENT), ("1.20.1-fabric", CHILD)):
            folder = data / "versions" / name
            folder.mkdir(parents=True)
            (folder / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")

        merged = versions.resolved_json(data, "1.20.1-fabric")
        checks = {
            "mainClass от дочерней": merged["mainClass"].endswith("KnotClient"),
            "javaVersion от родителя": merged["javaVersion"]["majorVersion"] == 17,
            "assetIndex от родителя": merged["assetIndex"]["id"] == "5",
            "тип от родителя": merged["type"] == "release",
            "id дочерней": merged["id"] == "1.20.1-fabric",
            "jar указывает на родителя": merged["jar"] == "1.20.1",
            "asm перекрыт дочерней": any(
                lib["name"] == "org.ow2.asm:asm:9.7.1" for lib in merged["libraries"]
            ),
            "старая asm убрана": not any(
                lib["name"] == "org.ow2.asm:asm:9.5" for lib in merged["libraries"]
            ),
            "brigadier остался": any(
                lib["name"] == "com.mojang:brigadier:1.0.18" for lib in merged["libraries"]
            ),
            "jvm-аргументы склеены": merged["arguments"]["jvm"][0].startswith("-Djava.library.path")
            and merged["arguments"]["jvm"][-1].startswith("-DFabricMcEmu"),
            "game-аргументы на месте": merged["arguments"]["game"] == ["--username", "${auth_player_name}"],
        }
        for name, ok in checks.items():
            print(("  OK   " if ok else "  FAIL ") + name)
            if not ok:
                failures.append(name)

        entries = versions.library_classpath_entries(merged, data / "libraries")
        paths = [str(path).replace("\\", "/") for path in entries]
        expected = [
            "libraries/com/mojang/brigadier/1.0.18/brigadier-1.0.18.jar",
            "libraries/org/ow2/asm/asm/9.7.1/asm-9.7.1.jar",
            "libraries/net/fabricmc/fabric-loader/0.16.9/fabric-loader-0.16.9.jar",
        ]
        rebuilt = [item.split("/minecraft/")[-1] if "/minecraft/" in item else item for item in paths]
        rebuilt = [item.split("/libraries/")[-1] for item in paths]
        ok = rebuilt == [item.replace("libraries/", "") for item in expected]
        print(("  OK   " if ok else "  FAIL ") + f"classpath по maven-координатам: {rebuilt}")
        if not ok:
            failures.append("classpath")

        source = versions.library_source({"name": "org.ow2.asm:asm:9.7.1", "url": "https://maven.fabricmc.net/"})
        print("  maven-ссылка:", source[0] if source else None)
        if not source or not source[0].endswith("/org/ow2/asm/asm/9.7.1/asm-9.7.1.jar"):
            failures.append("maven-ссылка")

    print("ИТОГ:", "всё сходится" if not failures else f"провалы: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
