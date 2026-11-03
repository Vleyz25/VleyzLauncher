import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vleyzlauncher.core import servers as core  # noqa: E402

for host in ("2b2t.org", "play.cubecraft.net", "minescape.net"):
    try:
        infos = socket.getaddrinfo(host, 25565)
        ip = infos[0][4][0]
    except OSError as exc:
        print(host, "DNS FAIL:", exc)
        continue
    try:
        with socket.create_connection((ip, 25565), timeout=4) as s:
            print(host, "TCP OK", ip)
    except OSError as exc:
        print(host, "TCP FAIL:", exc)
        continue
    print(host, "PING:", core.ping(host, timeout=6))
