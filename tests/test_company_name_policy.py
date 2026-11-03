"""公司名取值策略：列表卡简称 vs 详情页法人全称（2026-10-23）。

背景：真机探针（`tools/probe_company_scroll.py`）显示 Boss 详情页底部的公司名
是**注册主体全称**，列表卡才是**品牌简称**：

    列表卡：小红书        详情页底部：行吟信息科技（上海）有限公司
    列表卡：德勤          详情页底部：德勤企业咨询（上海）有限公司
    列表卡：拉卡拉        详情页底部：拉卡拉支付股份有限公司

「滚到底抓公司信息」修好之后，送给 LLM 的公司名就变成了法人全称——而 LLM 判
大厂**认品牌不认法人全称**，只给「行吟信息科技（上海）有限公司」它认不出小红书，
反而会把大厂判成非大厂（这正是这次修复要消除的漏判）。

所以取值策略定为：**列表卡简称优先，法人全称兜底**；两者都拿到时，把法人全称
一并交给 LLM（在 is_big_tech 判定话术里说明两者是同一家公司），既保住品牌识别，
又不丢雇主主体信息。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scenarios.boss.scripts.smart_match_greet import (  # noqa: E402
    _big_tech_hint_for,
    _legal_name_note,
    _looks_like_legal_name,
    _pick_company_name,
)


class TestPickCompanyName:
    def test_card_name_wins(self):
        """列表卡简称优先于详情页法人全称。"""
        display, legal = _pick_company_name(
            "小红书", "行吟信息科技（上海）有限公司", "")
        assert display == "小红书"
        assert legal == "行吟信息科技（上海）有限公司"

    def test_falls_back_to_legal_name(self):
        """列表卡为空时用详情页法人全称兜底。"""
        display, legal = _pick_company_name("", "拉卡拉支付股份有限公司", "")
        assert display == "拉卡拉支付股份有限公司"
        assert legal == "", "展示名已经是它了，不重复给"

    def test_falls_back_to_pre_expand(self):
        """列表卡 + 详情页底部都空时，用展开前读到的值兜底。"""
        display, legal = _pick_company_name("", "", "德勤")
        assert display == "德勤"
        assert legal == ""

    def test_all_empty(self):
        assert _pick_company_name("", "", "") == ("", "")

    def test_strips_whitespace(self):
        display, _ = _pick_company_name("  小红书  ", "", "")
        assert display == "小红书"

    def test_same_name_not_duplicated(self):
        """简称与全称字面相同时不重复给法人全称。"""
        display, legal = _pick_company_name("阿里巴巴", "阿里巴巴", "")
        assert display == "阿里巴巴"
        assert legal == ""


class TestLegalNameDetection:
    def test_detects_legal_names(self):
        for name in ("行吟信息科技（上海）有限公司",
                     "拉卡拉支付股份有限公司",
                     "上海罗莱智家生态科技（集团）有限公司"):
            assert _looks_like_legal_name(name), name

    def test_brand_names_are_not_legal(self):
        for name in ("小红书", "阿里健康", "德勤", "字节跳动"):
            assert not _looks_like_legal_name(name), name

    def test_empty_is_not_legal(self):
        assert not _looks_like_legal_name("")


class TestLegalNameNote:
    def test_note_when_both_names_present(self):
        note = _legal_name_note("小红书", "行吟信息科技（上海）有限公司")
        assert "行吟信息科技（上海）有限公司" in note
        assert "不要因名称陌生" in note

    def test_note_when_only_legal_name(self):
        """只有法人全称时也要提示 LLM：这是注册主体名，别因陌生就判否。"""
        note = _legal_name_note("拉卡拉支付股份有限公司")
        assert "法人全称" in note
        assert "不要仅因名称陌生就判否" in note

    def test_no_note_for_brand_only(self):
        assert _legal_name_note("小红书") == ""


class TestHintWiring:
    def test_hint_includes_legal_note(self):
        hint = _big_tech_hint_for(company="小红书",
                                  company_legal="行吟信息科技（上海）有限公司")
        assert "行吟信息科技（上海）有限公司" in hint

    def test_proxy_hint_keeps_proxy_semantics(self):
        """代理判定档位不能被法人全称说明覆盖掉。"""
        hint = _big_tech_hint_for(proxy_mode=True,
                                  company="", company_legal="")
        assert "不要因为信息缺失就判否" in hint

    def test_inferred_hint_kept(self):
        hint = _big_tech_hint_for(company_inferred=True, company="某科技")
        assert "从职位描述推断得到的" in hint

    def test_score_job_passes_legal_name(self):
        """score_job 必须把 job["company_legal"] 传进判定话术。"""
        src = (ROOT / "scenarios/boss/scripts/smart_match_greet.py").read_text("utf-8")
        assert '_company_legal = job.get("company_legal", "")' in src
        assert "company=_company, company_legal=_company_legal" in src

    def test_job_dict_carries_legal_name(self):
        src = (ROOT / "scenarios/boss/scripts/smart_match_greet.py").read_text("utf-8")
        assert '"company_legal":    _company_legal,' in src


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
