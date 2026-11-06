"""Limited Art-Net node discovery on private interfaces.

Sends ArtPoll only. Does not send DMX, does not change routes, and does not
pick a controller when more than one answers.
"""

from __future__ import annotations

import ipaddress
import socket

_HEADER = b"Art-Net\x00"
_POLL = 0x2000
_REPLY = 0x2100


def art_poll_packet() -> bytes:
    return _HEADER + _POLL.to_bytes(2, "little") + (14).to_bytes(2, "big") + bytes((0, 0))


def parse_poll_reply(data: bytes) -> str | None:
    if len(data) < 14 or not data.startswith(_HEADER):
        return None
    if int.from_bytes(data[8:10], "little") != _REPLY:
        return None
    return ".".join(str(part) for part in data[10:14])


def private_bind_addresses() -> list[tuple[str, str]]:
    """Return (bind_ip, /24 broadcast) for RFC1918 addresses. No route changes."""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    for info in infos:
        ip = str(info[4][0])
        if ip in seen:
            continue
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not addr.is_private or addr.is_loopback:
            continue
        seen.add(ip)
        parts = ip.split(".")
        found.append((ip, ".".join([*parts[:3], "255"])))
    return found


def discover_nodes(*, timeout_s: float = 0.6) -> list[str]:
    packet = art_poll_packet()
    sockets: list[socket.socket] = []
    nodes: list[str] = []
    try:
        for bind_ip, broadcast in private_bind_addresses():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(timeout_s)
            sock.bind((bind_ip, 0))
            sock.sendto(packet, (broadcast, 6454))
            sockets.append(sock)
        for sock in sockets:
            while True:
                try:
                    data, _addr = sock.recvfrom(1024)
                except TimeoutError:
                    break
                except OSError:
                    break
                node = parse_poll_reply(data)
                if node and node not in nodes:
                    nodes.append(node)
    finally:
        for sock in sockets:
            sock.close()
    return nodes
