"""
用 mDNS 发现 Pixel 上的 ADB 无线调试端点（2026-10-22）

手机开着「无线调试」时会广播 `_adb-tls-connect._tcp.local`，报文里直接带
**IP + 随机连接端口 + 机型/序列号**（TXT 记录 `name=Pixel 8a` / `api=36.0`）。
查它比扫端口快几个数量级。

Why:
- 2026-10-22 实战：手机 WiFi IP 从 `10.32.7.135` 漂到 `10.32.7.142`，
  对着旧 IP 扫了 40 分钟全端口段（32768–60999）全白费——mDNS 一条查询就定位了
- 无线调试的连接端口是**随机高位端口**，扫不出来也不该扫

Why not `adb mdns services`:
- 在这台 Pi 上它返回空（adb 内置 mDNS 客户端不可靠）
- `avahi-browse` 没装（装了 avahi-daemon 但没装 avahi-utils）
- 所以自己发 mDNS 查询（UDP 224.0.0.251:5353）解析 SRV/A/TXT

Manual usage:
    python -m utils.discover_adb_mdns              # 查 connect 服务
    python -m utils.discover_adb_mdns --pairing    # 查 pairing 服务（需手机开配对弹窗）
    python -m utils.discover_adb_mdns --watch 30   # 持续监听 30 秒
"""

from __future__ import annotations

import argparse
import socket
import struct
import time
from dataclasses import dataclass
from typing import Optional

_MDNS_ADDR = ("224.0.0.251", 5353)
_CONNECT_SERVICE = "_adb-tls-connect._tcp.local"
_PAIRING_SERVICE = "_adb-tls-pairing._tcp.local"


@dataclass
class Endpoint:
    """一个 mDNS 广播出来的 ADB 端点。"""

    host: str          # 手机 IP
    port: int          # 无线调试端口
    name: str = ""     # 服务实例名，如 adb-42231JEKB04971-3TK5bS
    txt: str = ""      # TXT 记录，如 ['api=36.0', 'name=Pixel 8a', 'v=1']

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"


def _build_query(service: str, qtype: int = 12) -> bytes:
    """构造一条 mDNS 查询报文（header + question）。"""
    q = b""
    for part in service.split("."):
        q += bytes([len(part)]) + part.encode()
    q += b"\x00"
    header = struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0)
    return header + q + struct.pack(">HH", qtype, 0x0001)


def _parse_name(data: bytes, offset: int) -> tuple[str, int]:
    """解析 DNS 名称，返回 (名称, 名称之后的偏移)。

    必须跟随 0xC0 压缩指针，否则像 Android 那样把 owner name 压缩引用的记录
    （A / PTR）会解析成空字符串，拼不上 SRV 的 target。
    """
    labels: list[str] = []
    next_offset = -1
    while True:
        length = data[offset]
        if length == 0:
            offset += 1
            if next_offset < 0:
                next_offset = offset
            break
        if length & 0xC0 == 0xC0:
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if next_offset < 0:
                next_offset = offset + 2
            offset = pointer
            continue
        labels.append(data[offset + 1:offset + 1 + length].decode("utf-8", "replace"))
        offset += 1 + length
    return ".".join(labels), next_offset


def _parse_txt(rdata: bytes) -> str:
    """解析 TXT 记录（key=value 列表）。"""
    parts = []
    i = 0
    while i < len(rdata):
        length = rdata[i]
        parts.append(rdata[i + 1:i + 1 + length].decode("utf-8", "replace"))
        i += 1 + length
    return ", ".join(parts)


def _parse_records(data: bytes) -> tuple[dict[str, str], list[tuple[str, int, str]], dict[str, str]]:
    """解析一个 mDNS 响应包。

    返回 (addresses, srv, txts)：
      addresses — owner name（小写）→ IP
      srv       — [(owner name, port, target name)]
      txts      — owner name（小写）→ TXT 文本

    两个坑（都踩过）：
    1. Android 把 SRV / TXT / A 放在 **Additional 段**，Answer 段只有 PTR
    2. SRV 的 rdata 里那个压缩指针指向**整包**偏移，必须用整包解析，越界会把整包丢掉
    """
    addresses: dict[str, str] = {}
    srv: list[tuple[str, int, str]] = []
    txts: dict[str, str] = {}
    qd = struct.unpack(">H", data[4:6])[0]
    counts = (
        struct.unpack(">H", data[6:8])[0],   # Answer
        struct.unpack(">H", data[8:10])[0],  # Authority
        struct.unpack(">H", data[10:12])[0],  # Additional
    )
    offset = 12
    try:
        for _ in range(qd):
            _, offset = _parse_name(data, offset)
            offset += 4
        for count in counts:
            for _ in range(count):
                name, offset = _parse_name(data, offset)
                rtype, _rclass, _ttl, rdlen = struct.unpack(">HHIH", data[offset:offset + 10])
                offset += 10
                rdata_offset = offset
                rdata = data[offset:offset + rdlen]
                offset += rdlen
                if rtype == 1:      # A
                    addresses[name.lower()] = ".".join(str(b) for b in rdata[:4])
                elif rtype == 33:   # SRV
                    _prio, _weight, port = struct.unpack(">HHH", rdata[:6])
                    target, _ = _parse_name(data, rdata_offset + 6)
                    srv.append((name, port, target))
                elif rtype == 16:   # TXT
                    txts[name.lower()] = _parse_txt(rdata)
    except (IndexError, struct.error):
        pass
    return addresses, srv, txts


def discover(service: str = _CONNECT_SERVICE, duration: float = 4.0) -> list[Endpoint]:
    """查询一次 mDNS，返回该服务下发现的端点。

    注意：SRV 与 A 记录**可能分在不同的响应包里**（实测 Android 就是这么发的），
    所以 A 记录按 owner name 缓存下来，等 SRV 到达时再拼。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", 0))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(1.0)
        sock.sendto(_build_query(service), _MDNS_ADDR)

        addresses: dict[str, str] = {}        # owner name → IP
        srv: list[tuple[str, int, str]] = []  # (owner name, port, target)
        txts: dict[str, str] = {}             # owner name → TXT
        deadline = time.time() + duration
        while time.time() < deadline:
            try:
                data, _ = sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                break
            _addresses, _srv, _txts = _parse_records(data)
            addresses.update(_addresses)
            srv.extend(_srv)
            txts.update(_txts)

        endpoints: dict[tuple[str, int], Endpoint] = {}
        for owner, port, target in srv:
            host = addresses.get(target.lower())
            if not host:
                continue
            key = (host, port)
            endpoints.setdefault(
                key,
                Endpoint(host=host, port=port, name=owner,
                         txt=txts.get(owner.lower(), "")),
            )
        return list(endpoints.values())
    finally:
        sock.close()


def watch(service: str = _CONNECT_SERVICE, duration: float = 30.0) -> None:
    """持续监听并打印新出现的端点（配对弹窗是临时开的，配对服务只广播一会儿）。"""
    seen: set[tuple[str, int]] = set()
    deadline = time.time() + duration
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", 0))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(1.0)
        while time.time() < deadline:
            try:
                sock.sendto(_build_query(service), _MDNS_ADDR)
            except OSError:
                pass
            try:
                data, _ = sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                break
            addresses, srv, txts = _parse_records(data)
            for owner, port, target in srv:
                host = addresses.get(target.lower())
                if not host:
                    continue
                key = (host, port)
                if key in seen:
                    continue
                seen.add(key)
                print(f"[{time.strftime('%H:%M:%S')}] {host}:{port}  "
                      f"{txts.get(owner.lower(), '')}", flush=True)
    finally:
        sock.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="mDNS 发现 ADB 无线调试端点")
    parser.add_argument("--pairing", action="store_true",
                        help="查配对服务（需手机打开「使用配对码配对设备」弹窗）")
    parser.add_argument("--watch", type=float, default=0,
                        help="持续监听 N 秒（配对服务只在弹窗打开期间广播）")
    parser.add_argument("--timeout", type=float, default=4.0, help="单次查询等待秒数")
    args = parser.parse_args()

    service = _PAIRING_SERVICE if args.pairing else _CONNECT_SERVICE
    if args.watch > 0:
        watch(service, args.watch)
        return
    endpoints = discover(service, args.timeout)
    if not endpoints:
        print(f"未发现 {service}（手机是否开着「无线调试」？）")
        return
    for ep in endpoints:
        print(f"{ep.address}  {ep.name}  {ep.txt}")


if __name__ == "__main__":
    main()
