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
        kws = load_keywords()
        for noise in ("1688", "UC", "QQ", "Riot", "POCO"):
            assert noise not in kws, f"{noise} 搜不出有效职位，应跳过"

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
