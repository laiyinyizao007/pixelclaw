"""
sweep_big_tech.py 单元测试（2026-10-21）。

覆盖：
- load_keywords：从 candidate_profile.yaml 的 big_tech_whitelist 生成搜索关键词
  - 母公司 + 旗下品牌都在列表里（用户 2026-10-20 确认「大厂含旗下品牌」）
  - 纯英文/拼音词条被过滤（Boss 是中文站，搜 "Alibaba" 没意义）
  - 母公司内部「短名是长名前缀」去重（阿里/阿里巴巴），但跨组不去重
    （「百度」是母公司、「百度网盘」是旗下品牌，前缀关系但都要留）
- _parse_sent：从子进程输出里解析发送条数
- run_one：命令行拼装（--only-big-tech / --max-greet / --device / --adb-reconnect-ip）
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scenarios.boss.scripts.sweep_big_tech import (  # noqa: E402
    _is_network_device,
    _parse_sent,
    load_keywords,
    run_one,
)


class TestLoadKeywords:
    def test_contains_parents_and_brands(self):
        kws = load_keywords()
        assert "字节跳动" in kws, "母公司必须在列表里"
        assert "抖音" in kws, "旗下品牌必须在列表里（用户确认大厂含旗下品牌）"
        assert "飞书" in kws
        assert "饿了么" in kws

    def test_filters_pure_ascii(self):
        """Boss 是中文站，纯英文词条搜不出对应公司。"""
        kws = load_keywords()
        assert "Alibaba" not in kws
        assert "Tencent" not in kws
        assert not any(k.isascii() for k in kws), "不应有纯 ASCII 关键词"

    def test_skips_noise_keywords(self):
        """噪音词条由 yaml 的 skip_keywords 控制（不在代码里硬编码品牌名）。"""
        import yaml

        kws = load_keywords()
        profile = yaml.safe_load(
            (ROOT / "scenarios" / "boss" / "config" / "candidate_profile.yaml").read_text(
                encoding="utf-8"
            )
        )
        skip = profile["big_tech_whitelist"]["skip_keywords"]
        assert skip, "yaml 里应声明 skip_keywords"
        for noise in skip:
            assert noise not in kws, f"{noise} 搜不出有效职位，应跳过"

    def test_no_company_names_hardcoded_in_script(self):
        """关键词噪音名单必须在 yaml，不能在代码里硬编码公司/品牌名。"""
        src = (ROOT / "scenarios" / "boss" / "scripts" / "sweep_big_tech.py").read_text(
            encoding="utf-8"
        )
        assert "_SKIP_KEYWORDS" not in src, "跳过名单不应硬编码在代码里"

    def test_dedups_parent_prefix_only(self):
        """母公司内部：短名是长名前缀 → 去掉短名。"""
        kws = load_keywords()
        assert "阿里" not in kws, "「阿里」是「阿里巴巴」的前缀，应去重"
        assert "字节" not in kws
        assert "蚂蚁" not in kws
        assert "阿里巴巴" in kws
        assert "字节跳动" in kws
        assert "蚂蚁集团" in kws

    def test_keeps_baidu_across_groups(self):
        """跨组前缀关系不去重：「百度」(母公司) 与「百度网盘」(旗下品牌) 都要留。"""
        kws = load_keywords()
        assert "百度" in kws
        assert "百度网盘" in kws

    def test_no_duplicates(self):
        kws = load_keywords()
        assert len(kws) == len(set(kws))

    def test_all_non_empty(self):
        assert all(k.strip() for k in load_keywords())


class TestParseSent:
    def test_parses_count(self):
        assert _parse_sent("✓ 打招呼成功：7 条") == 7

    def test_zero_when_missing(self):
        assert _parse_sent("搜索失败：xxx") == 0
        assert _parse_sent("") == 0


class TestRunOne:
    def test_cmd_contains_required_flags(self, monkeypatch):
        """跑一个关键词必须带上 --only-big-tech + 设备 + 重连目标。"""
        captured = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="✓ 打招呼成功：1 条", stderr="")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        got, out, code = run_one(
            "字节跳动", 5, "10.32.7.135:5555", 7, 18, 60,
        )
        cmd = captured["cmd"]
        assert "--only-big-tech" in cmd
        assert "--max-greet" in cmd and cmd[cmd.index("--max-greet") + 1] == "5"
        assert "--device" in cmd and cmd[cmd.index("--device") + 1] == "10.32.7.135:5555"
        # 断连自动重连要指向同一台设备，否则重连到别的 IP
        assert "--adb-reconnect-ip" in cmd
        assert cmd[cmd.index("--adb-reconnect-ip") + 1] == "10.32.7.135:5555"
        assert "--strict" in cmd
        assert got == 1 and code == 0

    def test_threshold_type_matches_child_argparse(self):
        """--threshold 的类型必须与子脚本一致。

        子脚本是 ``type=int``；sweep 曾用 ``type=float`` → 拼出 "7.0" →
        子脚本 argparse 直接报 "invalid int value" 退出 2，一条也发不出去。
        """
        import ast

        def _threshold_type(path: Path) -> str:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "--threshold"
                ):
                    for kw in node.keywords:
                        if kw.arg == "type":
                            return getattr(kw.value, "id", getattr(kw.value, "attr", "?"))
            return "?"

        sweep_t = _threshold_type(ROOT / "scenarios" / "boss" / "scripts" / "sweep_big_tech.py")
        child_t = _threshold_type(
            ROOT / "scenarios" / "boss" / "scripts" / "smart_match_greet.py"
        )
        assert sweep_t == child_t, f"threshold 类型不一致：sweep={sweep_t} child={child_t}"

    def test_child_rejects_float_threshold(self):
        """回归防线：子脚本确实拒绝 "7.0"（证明上一条断言有意义）。"""
        proc = subprocess.run(
            [
                sys.executable, str(ROOT / "scenarios" / "boss" / "scripts" / "smart_match_greet.py"),
                "--threshold", "7.0",
            ],
            capture_output=True, text=True, timeout=60,
        )
        assert "invalid int value" in (proc.stderr or "")

    def test_assembled_cmd_is_accepted_by_child_argparse(self, monkeypatch):
        """把真实拼出的命令行喂给子脚本的 argparse（--help 收尾，无副作用）。

        这是最贴近真跑的回归防线：参数类型/拼写对不上时，子脚本会直接
        exit 2 拒绝执行——sweep 却会把「0 条」当成正常结果继续跑。
        """
        captured = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        run_one("字节跳动", 5, "10.32.7.135:5555", 7, 18, 60)

        proc = subprocess.run(
            captured["cmd"] + ["--help"], capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, (
            f"子脚本拒绝 sweep 拼出的命令行：\n{proc.stderr[-800:]}"
        )

    def test_omits_device_flags_when_not_given(self, monkeypatch):
        captured = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        run_one("腾讯", 3, None, 7, 18, 60)
        cmd = captured["cmd"]
        assert "--device" not in cmd
        assert "--adb-reconnect-ip" not in cmd

    def test_usb_serial_device_does_not_set_reconnect_ip(self, monkeypatch):
        """USB serial 不是网络地址，--adb-reconnect-ip 不能照抄它。"""
        captured = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        run_one("腾讯", 3, "42231JEKB04971", 7, 18, 60)
        cmd = captured["cmd"]
        assert cmd[cmd.index("--device") + 1] == "42231JEKB04971"
        assert "--adb-reconnect-ip" not in cmd

    def test_timeout_keeps_partial_output(self, monkeypatch):
        """超时也要保住已发出的条数（子命令以 -u 无缓冲启动）。"""

        def _fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 60, output="✓ 打招呼成功：3 条".encode())

        monkeypatch.setattr(subprocess, "run", _fake_run)
        got, out, code = run_one("腾讯", 5, None, 7, 18, 60)
        assert got == 3
        assert code == -1


class TestIsNetworkDevice:
    def test_host_port(self):
        assert _is_network_device("10.32.7.135:5555") is True

    def test_usb_serial(self):
        assert _is_network_device("42231JEKB04971") is False

    def test_serial_with_colon(self):
        assert _is_network_device("emulator-5554:5555") is False
