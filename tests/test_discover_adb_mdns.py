"""mDNS 发现 ADB 无线调试端点的回归测试（2026-10-22）。

背景：手机开着「无线调试」时广播 `_adb-tls-connect._tcp.local`，报文里带 IP + 端口。
2026-10-22 实战中靠这个一条查询就定位了漂移后的手机（`10.32.7.142:44547`），
此前对着旧 IP 扫了 40 分钟全端口段全白费。

解析踩过的三个坑（都有回归断言）：
1. Android 把 SRV / TXT / A 放在 **Additional 段**，Answer 段只有 PTR
2. SRV 的 rdata 里那个压缩指针指向**整包**偏移——在 rdata 切片里解析会越界，
   越界异常会把整包丢掉（表现为"发现不到任何端点"）
3. 压缩指针不跟随时，owner name 解析成空串，拼不上 SRV 的 target

用真实抓包的字节布局构造合成报文，不依赖网络。
"""
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from utils.discover_adb_mdns import (  # noqa: E402
    Endpoint,
    _build_query,
    _parse_name,
    _parse_records,
    _parse_txt,
    discover,
)


def _encode_name(name: str) -> bytes:
    out = b""
    for part in name.split("."):
        out += bytes([len(part)]) + part.encode()
    return out + b"\x00"


def _record(name: bytes, rtype: int, rdata: bytes) -> bytes:
    return name + struct.pack(">HHIH", rtype, 0x0001, 120, len(rdata)) + rdata


def _build_android_like_packet(
    host: str = "10.32.7.142",
    port: int = 44547,
    instance: str = "adb-42231JEKB04971-3TK5bS",
    service: str = "_adb-tls-connect._tcp.local",
) -> bytes:
    """按 Android 实测布局造一个响应包。

    - Answer 段：1 条 PTR
    - Additional 段：SRV（target 用压缩指针指回 A 记录 owner 名里的 "local" 标签）、TXT、A
    """
    header = struct.pack(">HHHHHH", 0, 0x8400, 1, 1, 0, 3)   # 12 字节
    question = _encode_name(service)
    question_tail = b"\x00\x01\x00\x01"                    # qtype=Q + qclass=IN
    ptr_rdata = bytes([len(instance)]) + instance.encode() + b"\xc0\x0c"
    answer = _record(_encode_name(service), 12, ptr_rdata)

    srv_owner = f"{instance}.{service}"
    target_label = "Android-2"
    a_owner = bytes([len(target_label)]) + target_label.encode() + b"\x05local\x00"
    txt_rdata = b"\x08api=36.0\rname=Pixel 8a\x03v=1"

    # 偏移推算（顺序：question → answer → srv → txt → a）
    # question 段 = name + type(2) + class(2)，所以答案段从 name 之后 4 字节开始
    q_end = 12 + len(question) + 4
    a_end = q_end + len(answer)
    srv_rdata_len = 6 + 1 + len(target_label) + 2   # prio/weight/port + label + 压缩指针
    srv_len = len(_encode_name(srv_owner)) + 10 + srv_rdata_len
    txt_len = len(_encode_name(srv_owner)) + 10 + len(txt_rdata)
    a_owner_offset = a_end + srv_len + txt_len
    local_label_offset = a_owner_offset + 1 + len(target_label)

    srv_rdata = (
        struct.pack(">HHH", 0, 0, port)
        + bytes([len(target_label)]) + target_label.encode()
        + struct.pack(">H", 0xC000 | local_label_offset)
    )
    srv = _record(_encode_name(srv_owner), 33, srv_rdata)
    txt = _record(_encode_name(srv_owner), 16, txt_rdata)
    a_rec = _record(a_owner, 1, bytes(int(x) for x in host.split(".")))

    return header + question + question_tail + answer + srv + txt + a_rec


class TestParseName:
    def test_plain_name(self):
        data = _encode_name("a.b.local")
        name, offset = _parse_name(data, 0)
        assert name == "a.b.local"
        assert offset == len(data)

    def test_follows_compression_pointer(self):
        """压缩指针必须跟随，否则 owner name 解析成空串。"""
        buf = b"\x09Android-2\xc0\x0c" + b"\x05local\x00"
        name, offset = _parse_name(buf, 0)
        assert name == "Android-2.local"
        assert offset == 12, "返回的是原始名称结束处的偏移（指针占 2 字节）"


class TestParseTxt:
    def test_parses_key_values(self):
        rdata = b"\x08api=36.0\rname=Pixel 8a\x03v=1"
        text = _parse_txt(rdata)
        assert "api=36.0" in text
        assert "name=Pixel 8a" in text
        assert "v=1" in text


class TestParseRecords:
    def test_finds_srv_txt_a_in_additional_section(self):
        data = _build_android_like_packet()
        addresses, srv, txts = _parse_records(data)
        assert len(srv) == 1
        owner, port, target = srv[0]
        assert port == 44547
        assert addresses.get(target.lower()) == "10.32.7.142", (
            "A 记录在 Additional 段，且 owner 名要能跟 SRV 的 target 对上"
        )
        assert "Pixel 8a" in txts.get(owner.lower(), "")

    def test_pointer_into_full_packet_is_resolved(self):
        """SRV 的 rdata 压缩指针指向整包偏移，不能在切片里解析。"""
        data = _build_android_like_packet()
        addresses, srv, _ = _parse_records(data)
        assert srv, "解析不能因越界把整包丢掉"
        assert addresses


class TestDiscover:
    def test_discover_returns_list(self):
        """网络上有无设备都不应抛异常，只影响返回内容。"""
        result = discover("_adb-tls-connect._tcp.local", duration=0.5)
        assert isinstance(result, list)
        for ep in result:
            assert isinstance(ep, Endpoint)
            assert ep.host and ep.port > 0

    def test_endpoint_address_format(self):
        ep = Endpoint(host="10.32.7.142", port=44547)
        assert ep.address == "10.32.7.142:44547"


class TestBuildQuery:
    def test_query_has_question_count_one(self):
        q = _build_query("_adb-tls-connect._tcp.local")
        assert struct.unpack(">H", q[4:6])[0] == 1
        assert b"_adb-tls-connect" in q


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
