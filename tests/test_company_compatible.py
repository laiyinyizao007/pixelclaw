"""
公司名跨视图兼容判定单测（2026-10-21）。

背景：2026-10-21 真机跑「仅大厂」sweep 时，字节跳动一张卡
（产品体验设计专家（UX）-中国广告产品 / 马艺洁）被反复处理 15 次、0 产出。
根因是 Boss 三个视图给的公司名不一致：

    列表卡：字节跳动
    详情页：北京字跳网络技术有限公司
    聊天页：字节跳动

旧逻辑（前 6 字 INSTR + bigram）判定「不是同一家公司」，后果有二：
  1. 聊天页公司校验拦下合法发送 —— 此时 Boss 已自动发了默认招呼，
     我们的个性化招呼没发出去，DB 也没记账；
  2. job_visits 去重查不到记录 → 兜底放行 → 同卡无限重试，白烧 LLM 配额。

覆盖：
- _company_core：剥行政区前缀 + 公司形式通用词
- _company_compatible：等价 / 子串 / bigram / 剥词字符集 五档判定
- 关键反例：「小米」vs「小鹏汽车」不能判成同一家
- 源码防回归：聊天页校验必须走 _company_compatible，不能回到旧的 [:6] INSTR
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scenarios.boss.scripts.smart_match_greet import (  # noqa: E402
    _company_compatible,
    _company_core,
)

SRC = (ROOT / "scenarios/boss/scripts/smart_match_greet.py").read_text(encoding="utf-8")


class TestCompanyCore:
    """剥行政区前缀 + 公司形式通用词。"""

    def test_strips_geo_prefix(self):
        assert _company_core("北京字节跳动") == "字节跳动"
        assert _company_core("深圳市腾讯") == "腾讯"

    def test_strips_corporate_form_tokens(self):
        assert _company_core("北京字跳网络技术有限公司") == "字跳"
        assert _company_core("上海寻梦信息技术有限公司") == "寻梦"

    def test_keeps_brand_name(self):
        assert _company_core("字节跳动") == "字节跳动"
        assert _company_core("美团") == "美团"
        assert _company_core("智谱") == "智谱"

    def test_empty_input(self):
        assert _company_core("") == ""

    def test_falls_back_when_stripped_to_empty(self):
        """剥光后回退原名，避免把壳公司名当核心。"""
        assert _company_core("有限公司") == "有限公司"


class TestCompanyCompatible:
    """五档判定：等价 / 子串 / bigram / 剥词字符集。"""

    def test_exact(self):
        assert _company_compatible("字节跳动", "字节跳动")

    def test_empty_degrades_to_true(self):
        """字段缺失时不该判成两家公司。"""
        assert _company_compatible("", "字节跳动")
        assert _company_compatible("字节跳动", "")
        assert _company_compatible("", "")

    def test_brand_vs_legal_entity(self):
        """本次 bug 的核心用例：列表卡品牌名 vs 详情页法定全称。"""
        assert _company_compatible("字节跳动", "北京字跳网络技术有限公司")

    def test_bigram_case_keeps_working(self):
        """2026-10-19 修过的「融和科技」变体不能回归。"""
        assert _company_compatible("融和科技", "北京融和友信科技股份有限公司")

    def test_containment_case(self):
        assert _company_compatible("百度", "百度在线网络技术（北京）有限公司")
        assert _company_compatible("腾讯", "深圳市腾讯计算机系统有限公司")

    def test_different_companies_rejected(self):
        assert not _company_compatible("字节跳动", "阿里巴巴")
        assert not _company_compatible("美团", "拼多多")

    def test_short_name_confusion_rejected(self):
        """「小米」vs「小鹏汽车」只共享 1 个字 → 不能判成同一家。"""
        assert not _company_compatible("小米", "小鹏汽车")

    def test_unrelated_rejected(self):
        assert not _company_compatible("某不知名创业公司", "字节跳动")


class TestChatGuardUsesCompatible:
    """防回归：聊天页公司校验必须走 _company_compatible，不能回到旧的 [:6] INSTR。"""

    def test_no_legacy_prefix_match_in_chat_guard(self):
        assert "company[:6] in _chat_company_text" not in SRC, (
            "聊天页公司校验已改用 _company_compatible，禁止退回前 6 字 INSTR"
        )

    def test_chat_guard_calls_compatible(self):
        assert "_chat_company_match = _company_compatible(company, _chat_company_text)" in SRC

    def test_find_next_uses_compatible(self):
        assert "_company_compatible(list_co, _row_co)" in SRC, (
            "job_visits 去重必须用 _company_compatible 判同一家公司"
        )

    def test_session_revisit_cap_exists(self):
        """跨视图去重链断裂时的硬闸兜底：同一职位本轮最多处理 2 次。"""
        assert "_session_job_counts" in SRC
        assert "本轮重复处理超限" in SRC
