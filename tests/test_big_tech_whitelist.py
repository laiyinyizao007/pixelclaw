"""
大厂 LLM 评判 + 仅大厂发招呼单元测试（2026-10-20）。

设计变更：
- 早期版本用 hard-string-match (`is_big_tech()` 函数) 维护成本高、难穷举；
- 现改为：candidate_profile.yaml 的 big_tech_whitelist 仅作 prompt 上下文注入，
  LLM 在 score_job 时自主判断 is_big_tech + big_tech_reasoning。
- 过滤在评分之后做（让 LLM 看到 JD 描述），开销 ≈ 多 50 tokens / call。

覆盖：
- `_build_big_tech_context()` 注入逻辑（whitelist=None / 空 / 完整 / 超长截断）
- score_job prompt 模板 format 时新字段不报错（{big_tech_context} + {big_tech_judge_hint}）
- argparse --only-big-tech flag 仍存在
- yaml big_tech_whitelist 段可被安全加载且格式合规
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scenarios.boss.scripts import smart_match_greet as smg  # noqa: E402
from scenarios.boss.scripts.smart_match_greet import (  # noqa: E402
    _build_big_tech_context,
    _load_prompt,
)


# ───────────────────────────── _build_big_tech_context ─────────────────────────────


class TestBuildBigTechContext:
    """whitelist 注入逻辑：None / 空 dict / 部分填充 / 超长截断。"""

    def test_none_returns_empty(self):
        assert _build_big_tech_context(None) == ""

    def test_empty_dict_returns_empty(self):
        assert _build_big_tech_context({}) == ""

    def test_empty_parents_and_subsidiaries_returns_criteria_only(self):
        """空 parents + 空 subsidiaries 仍返判定标准段（LLM 可见大厂定义）。
        只有 None 或 {} 才完全返空。"""
        text = _build_big_tech_context({"parents": [], "subsidiaries": {}})
        assert "判定标准" in text
        assert "母公司样本" not in text  # 列表段不出现
        assert "旗下品牌" not in text

    def test_only_parents(self):
        text = _build_big_tech_context({"parents": ["字节跳动", "腾讯"], "subsidiaries": {}})
        assert "母公司样本" in text
        assert "字节跳动" in text
        assert "腾讯" in text
        assert "旗下品牌" not in text

    def test_only_subsidiaries(self):
        text = _build_big_tech_context({"parents": [], "subsidiaries": {"字节": ["抖音", "飞书"]}})
        assert "母公司样本" not in text
        assert "旗下品牌" in text
        assert "字节" in text
        assert "抖音" in text

    def test_truncates_parents_at_30(self):
        """超过 30 家 parents 时截断，避免 prompt 过长。"""
        long_parents = [f"公司{i}" for i in range(50)]
        text = _build_big_tech_context({"parents": long_parents, "subsidiaries": {}})
        # 出现 "等" 表示截断
        assert "等" in text
        # "公司0-29" 出现，"公司49" 不出现（前 30 出现）
        assert "公司29" in text

    def test_truncates_subsidiaries_brands_at_5(self):
        """每个母公司最多展示 5 个品牌。"""
        long_brands = [f"品牌{i}" for i in range(10)]
        text = _build_big_tech_context({"parents": [], "subsidiaries": {"字节": long_brands}})
        # 品牌 0-4 出现，品牌 5+ 不出现
        assert "品牌0" in text
        assert "品牌4" in text
        assert "品牌5" not in text

    def test_truncates_subsidiaries_parents_at_8(self):
        """subsidiaries 母公司超过 8 个时截断。"""
        sub = {f"母公司{i}": [f"品牌{i}"] for i in range(15)}
        text = _build_big_tech_context({"parents": [], "subsidiaries": sub})
        # 母公司 0-7 出现，8+ 不出现
        assert "母公司7" in text
        assert "母公司8" not in text

    def test_includes_criteria_summary(self):
        """判定标准段始终出现（LLM 可见大厂定义）。"""
        text = _build_big_tech_context({"parents": ["字节跳动"], "subsidiaries": {}})
        assert "判定标准" in text
        assert "BAT" in text or "互联网头部" in text
        assert "AI 新势力" in text


# ───────────────────────────── prompt 模板 format 不报错 ─────────────────────────────


class TestPromptFormatting:
    """match_score.md prompt 新加 {big_tech_context} 和 {big_tech_judge_hint} 占位符
    必须能被 .format() 替换，不报 KeyError。"""

    def test_prompt_template_has_new_placeholders(self):
        tpl = _load_prompt("match_score")
        assert "{big_tech_context}" in tpl
        assert "{big_tech_judge_hint}" in tpl

    def test_format_with_whitelist_succeeds(self):
        """完整 format 不抛 KeyError。"""
        tpl = _load_prompt("match_score")
        out = tpl.format(
            resume="r",
            title="AI产品经理",
            company="字节跳动",
            salary="30-50K",
            experience="3-5年",
            company_info="1000-9999人 • C轮",
            hr_name="张总",
            description_excerpt="负责 ToB Agent",
            requirements="AI",
            strict_rules="",
            scoring_rubric="rubric",
            big_tech_context=_build_big_tech_context({"parents": ["字节跳动"], "subsidiaries": {}}),
            big_tech_judge_hint="hint",
        )
        assert "{big_tech_context}" not in out  # 已被替换
        assert "字节跳动" in out
        assert "hint" in out

    def test_format_with_empty_whitelist_succeeds(self):
        """whitelist=None 时 big_tech_context="" 也必须 format 不报错。"""
        tpl = _load_prompt("match_score")
        out = tpl.format(
            resume="r",
            title="t",
            company="c",
            salary="s",
            experience="e",
            company_info="ci",
            hr_name="h",
            description_excerpt="d",
            requirements="r",
            strict_rules="",
            scoring_rubric="rubric",
            big_tech_context=_build_big_tech_context(None),
            big_tech_judge_hint="hint for empty mode",
        )
        assert "{big_tech_context}" not in out
        assert "{big_tech_judge_hint}" not in out
        # 模板里原"大厂参考清单"段是空的，但 hint 仍注入
        assert "hint for empty mode" in out


# ───────────────────────────── yaml 段加载 + 防退化 ─────────────────────────────


class TestProfileYamlLoads:
    """candidate_profile.yaml:big_tech_whitelist 段可被 yaml.safe_load 解析。
    所有 parents / brands 至少 2 字符（防空白字符串全匹配）。
    """

    def test_yaml_whitelist_loads_and_has_keywords(self):
        import yaml

        profile = yaml.safe_load(
            (ROOT / "scenarios/boss/config/candidate_profile.yaml").read_text()
        )
        wl = profile["big_tech_whitelist"]
        assert wl["parents"], "parents 必须非空"
        assert wl["subsidiaries"], "subsidiaries 必须非空"

        for p in wl["parents"]:
            assert isinstance(p, str) and len(p) >= 2, f"parent 异常: {p!r}"

        for parent, brands in wl["subsidiaries"].items():
            for b in brands:
                assert isinstance(b, str) and len(b) >= 2, (
                    f"subsidiary brand 异常: {parent!r} -> {b!r}"
                )


# ───────────────────────────── argparse 接线 ─────────────────────────────


class TestMainArgparse:
    """--only-big-tech 旗标被 argparse 接受。"""

    def test_help_only_big_tech_listed(self):
        r = subprocess.run(
            [sys.executable, "scenarios/boss/scripts/smart_match_greet.py", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert "--only-big-tech" in r.stdout, (
            f"--only-big-tech 未在 --help 中显示:\n{r.stdout}"
        )


# ───────────────────────────── 旧 is_big_tech() 已删除（防回归）─────────────────────────────


class TestNoHardFilterRemains:
    """纯 LLM 判定——代码里不得残留任何字符串白名单匹配。

    历史：早期版本用 `is_big_tech(company, whitelist)` hard substring 过滤，
    后来又留了 `_is_big_tech_string_match()` 作 fallback。2026-10-20 用户要求
    「不是硬过滤，用 agent 判断」→ 两者全部删除。LLM 漏输出 is_big_tech 时
    放行 + warning，不退回字符串匹配。
    """

    def test_is_big_tech_function_not_exported(self):
        assert not hasattr(smg, "is_big_tech"), (
            "is_big_tech() 已被 LLM 评判替代，禁止重新加入模块"
        )

    def test_string_match_fallback_not_exported(self):
        assert not hasattr(smg, "_is_big_tech_string_match"), (
            "_is_big_tech_string_match() 已删除——大厂判定必须纯 LLM，禁止加回硬匹配"
        )

    def test_source_has_no_company_substring_filter(self):
        """源码里不存在对公司名做 substring 匹配的过滤逻辑。

        精确判据：whitelist 的 parents / subsidiaries 键在整个模块里**只出现一次**
        ——即 `_build_big_tech_context()` 里读出来拼 prompt。若出现第二次，就说明
        有人在别处拿它做字符串匹配过滤。
        """
        src = (ROOT / "scenarios/boss/scripts/smart_match_greet.py").read_text(
            encoding="utf-8"
        )
        assert "_is_big_tech_string_match" not in src
        assert src.count('"parents"') == 1, (
            f"whitelist['parents'] 应只在 _build_big_tech_context 出现一次，"
            f"实际 {src.count(chr(34) + 'parents' + chr(34))} 次——疑似被用于字符串匹配过滤"
        )
        assert src.count('"subsidiaries"') == 1, (
            f"whitelist['subsidiaries'] 应只在 _build_big_tech_context 出现一次，"
            f"实际 {src.count(chr(34) + 'subsidiaries' + chr(34))} 次"
        )

    def test_llm_field_is_read_from_scored_result(self):
        """is_big_tech 只能从 LLM 返回的 scored 里读——不是从公司名字符串推的。"""
        src = (ROOT / "scenarios/boss/scripts/smart_match_greet.py").read_text(
            encoding="utf-8"
        )
        assert 'scored.get("is_big_tech")' in src, (
            "大厂判定必须读 LLM 输出的 scored['is_big_tech']"
        )