"""详情页公司信息块滚动抓取（2026-10-23）。

背景：2026-10-22 有 **18.6%（41/220）** 的访问拿不到公司名，26 条纯因此被
LLM 判「非大厂」而拒发。根因（见 `scenarios/boss/docs/atomic_operations.md`
§6.1.4 与 `skills/boss/boss_automation_skill.py` 的 `scroll_for_company_info`）：

- 公司名 `tv_com_name`、公司信息 `tv_com_info`、地点 `tv_location` 都在详情页
  **底部**的公司信息块里，**不在顶部头部**；
- 该块由外层 `rv_list` RecyclerView **懒加载**：inflate 之前页面滚不动，连续
  多轮 dump 字节完全相同，之后才突然长出整块内容 → 「XML 未变」不能判定到底，
  要连续 N 轮不变才退出。

所以修法是**滚到底抓一次**，而不是在顶部等渲染。本文件把这条修复钉死：

1. 滚到底后能把公司名合进 detail
2. 拿到公司名即停（不白滚）
3. 连续 N 轮 dump 相同 → 判定到底，退出（不无限滚）
4. 滚动轮数有上限
5. 滚出「网络异常」占位页 → 立即停（占位页不可滚）
6. 职位标题变了 → 立即停（防止把别的公司的信息并进来）
7. 调用点在 `expand_description()` **之后**（展开必须在大幅滚动之前做）
8. 展开前读到的公司名仍作为回落
"""
import ast
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from skills.boss.boss_automation_skill import BOSSAutomationSkill  # noqa: E402


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """滚动抓取里有 sleep(0.5)/sleep(1.0)，测试里全部跳过。"""
    monkeypatch.setattr("skills.boss.boss_automation_skill.time.sleep", lambda *_: None)


class FakeSkill(BOSSAutomationSkill):
    """假 skill：只实现滚动抓取用到的三个动作。"""

    def __init__(self, xmls, detail_by_xml=None):
        self._logger = logging.getLogger("test")
        self.xmls = list(xmls)
        self.detail_by_xml = detail_by_xml or {}
        self.scrolls = 0
        self._xml_idx = 0

    def scroll_down(self, start_y=1500, end_y=800, duration=300):
        self.scrolls += 1

    def get_ui_hierarchy(self, force_refresh=False):
        idx = min(self._xml_idx, len(self.xmls) - 1)
        self._xml_idx += 1
        return self.xmls[idx]

    def get_job_detail(self, xml=None):
        return self.detail_by_xml.get(xml, {})


def _xml(tag):
    return f"<hierarchy><node text='{tag}'/></hierarchy>"


class TestScrollForCompanyInfo:
    def test_finds_company_after_scrolling(self):
        """滚到底后公司名要能合进 detail。"""
        skill = FakeSkill(
            [_xml("a"), _xml("b"), _xml("c")],
            {_xml("c"): {"title": "AI产品经理", "company": "字节跳动"}},
        )
        detail = {"title": "AI产品经理"}
        out = skill.scroll_for_company_info(detail, max_scrolls=5, max_stale=3)
        assert out["company"] == "字节跳动"
        assert skill.scrolls > 0

    def test_stops_as_soon_as_company_found(self):
        """拿到公司名即停，不白滚后面的轮次。"""
        skill = FakeSkill(
            [_xml("a"), _xml("b"), _xml("c")],
            {_xml("b"): {"title": "AI产品经理", "company": "快手"}},
        )
        out = skill.scroll_for_company_info({"title": "AI产品经理"}, max_scrolls=10)
        assert out["company"] == "快手"
        assert skill.scrolls == 2, "命中后不应继续滚"

    def test_stops_when_dumps_are_identical(self):
        """连续 N 轮 dump 相同 = 已到底（块没 inflate），必须退出。"""
        skill = FakeSkill([_xml("same")], {})
        out = skill.scroll_for_company_info({"title": "x"}, max_scrolls=30, max_stale=3)
        assert "company" not in out
        assert skill.scrolls == 4, "首轮建立基准 + 连续 3 轮不变即退出"

    def test_scroll_count_is_bounded(self):
        """滚动轮数有上限，不能无限滚。"""
        skill = FakeSkill([_xml("a"), _xml("b"), _xml("c")], {})
        skill.scroll_for_company_info({"title": "x"}, max_scrolls=4, max_stale=99)
        assert skill.scrolls <= 4

    def test_stops_on_network_error_page(self):
        """滚出网络异常占位页 → 立即停（占位页不可滚）。"""
        skill = FakeSkill([_xml("网络异常")], {})
        out = skill.scroll_for_company_info({"title": "x"}, max_scrolls=10, max_stale=3)
        assert skill.scrolls == 1, "碰到网络异常就应停止滚动"

    def test_stops_when_title_changes(self):
        """职位标题变了 → 停止，不能把别的公司的信息并进来。"""
        skill = FakeSkill(
            [_xml("a"), _xml("b")],
            {_xml("b"): {"title": "另一个岗位", "company": "别家公司"}},
        )
        out = skill.scroll_for_company_info({"title": "AI产品经理"}, max_scrolls=5, max_stale=3)
        assert "company" not in out, "标题变了就不能采信抓到的公司名"

    def test_merge_keeps_longer_description(self):
        """合并时描述保留更长的一份（滚动后描述会滚出屏幕）。"""
        skill = FakeSkill(
            [_xml("a")],
            {_xml("a"): {"description": "短"}},
        )
        out = skill.scroll_for_company_info(
            {"title": "x", "description": "这是一段更长的职位描述"}, max_scrolls=1)
        assert out["description"] == "这是一段更长的职位描述"

    def test_merge_does_not_overwrite_existing_company(self):
        """先到的公司名不被后来的空值覆盖。"""
        skill = FakeSkill([_xml("a")], {_xml("a"): {"company": ""}})
        out = skill.scroll_for_company_info({"title": "x", "company": "美团"}, max_scrolls=1)
        assert out["company"] == "美团"


class TestCallSite:
    """调用点时序必须正确。"""

    @staticmethod
    def _source():
        return (ROOT / "scenarios/boss/scripts/smart_match_greet.py").read_text("utf-8")

    def test_scroll_call_exists(self):
        assert "skill.scroll_for_company_info(detail)" in self._source()

    def test_scroll_happens_after_expand_description(self):
        """展开 JD 必须在大幅滚动之前做（见 atomic_operations.md §6.2.1）。"""
        src = self._source()
        i_expand = src.index("skill.expand_description()")
        i_scroll = src.index("skill.scroll_for_company_info(detail)")
        assert i_scroll > i_expand, "滚动抓公司信息必须排在展开描述之后"

    def test_scroll_happens_before_llm_scoring(self):
        """抓公司信息必须在评分（LLM 调用）之前。"""
        src = self._source()
        i_scroll = src.index("skill.scroll_for_company_info(detail)")
        i_fallback = src.index("infer_recruiter_company(client, job_dict")
        assert i_scroll < i_fallback, "先拿到公司名，再让 LLM 判"

    def test_job_dict_falls_back_to_pre_expand_company(self):
        """展开前读到的公司名仍作为回落。"""
        src = self._source()
        assert 'detail.get("company") or _pre_expand_company or company' in src

    def test_scroll_helper_is_skill_method(self):
        """滚动抓取实现放在 skill 里（复用于 scraper 流程）。"""
        tree = ast.parse((ROOT / "skills/boss/boss_automation_skill.py").read_text("utf-8"))
        cls = next(n for n in tree.body
                   if isinstance(n, ast.ClassDef) and n.name == "BOSSAutomationSkill")
        names = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)]
        assert "scroll_for_company_info" in names


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
