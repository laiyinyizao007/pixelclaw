"""招聘主体缺失时的两级兜底判定（2026-10-22）。

背景：10-21 / 10-22 的「LLM 判非大厂」里有 44% 是**判定输入缺失**造成的——
Boss 上第三方主体 / 客户项目 / 灵活用工发布的岗位，`company` 与 `company_info`
两个字段都是空的，LLM 手里只剩分数，保守判否。昨天有 7 条的标题/JD 直接写着
业务线（抖音电商 / DeepSeek / 华为项目 / 点评打卡 / 京东），白白丢掉。

用户定的方案是**先 2 再 1**：
1. 先单独调一次 LLM，从 JD **反推招聘主体名**，用反推出的名字走现有 is_big_tech 判定
2. 反推不出主体时，退回**业务域代理判定**（判断该岗位业务场景/职能/薪资是否
   属于头部企业的典型业务线），而不是因为信息缺失就判否

**约束（用户明确要求）**：不硬编码公司名。yaml 白名单只作为 LLM 的参考样本，
新增的推断 prompt 与兜底话术里都**不允许出现具体公司名**——本文件用
`test_no_hardcoded_company_names` 把这条钉死。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scenarios.boss.scripts import smart_match_greet as smg  # noqa: E402


class FakeResponse:
    def __init__(self, text):
        self.content = [type("C", (), {"text": text})()]


class FakeClient:
    """假 Anthropic client：按调用顺序返回预设文本。"""

    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = []

    # 真实调用是 client.messages.create(**kwargs)
    class _Messages:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kwargs):
            self.outer.calls.append(kwargs)
            return FakeResponse(self.outer.texts[min(len(self.outer.calls) - 1,
                                                      len(self.outer.texts) - 1)])

    @property
    def messages(self):
        return FakeClient._Messages(self)


def _job(**kw):
    base = {
        "title": "电商B端产品- AI 产品经理（商家经营方向）",
        "company": "",
        "company_info": "",
        "salary_raw": "35-65K·16薪",
        "experience": "3-5年",
        "description": "基于商家经营痛点（经营难题、流量分发、转化瓶颈），设计业务解决方案。",
        "hr_name": "王先生",
    }
    base.update(kw)
    return base


class TestInferRecruiterCompany:
    def test_parses_company_confidence_evidence(self):
        client = FakeClient(json.dumps({
            "company": "字节跳动", "confidence": "medium",
            "evidence": "JD 提到商家经营，是抖音电商典型业务",
        }))
        name, conf, ev = smg.infer_recruiter_company(client, _job())
        assert name == "字节跳动"
        assert conf == "medium"
        assert "商家经营" in ev

    def test_prompt_contains_job_evidence(self):
        client = FakeClient(json.dumps({"company": "字节跳动", "confidence": "high",
                                        "evidence": "x"}))
        smg.infer_recruiter_company(client, _job())
        prompt = client.calls[0]["messages"][0]["content"]
        assert "商家经营" in prompt, "JD 正文必须进 prompt，否则无从推断"
        assert "35-65K" in prompt, "薪资也是判断体量的重要线索"
        assert "商家经营方向" in prompt, "职位标题里的业务线线索也要给"

    def test_json_fence_tolerated(self):
        client = FakeClient("```json\n" + json.dumps(
            {"company": "美团", "confidence": "low", "evidence": "e"}) + "\n```")
        name, _, _ = smg.infer_recruiter_company(client, _job())
        assert name == "美团"

    def test_unparseable_response_returns_empty(self):
        client = FakeClient("完全不是 JSON")
        assert smg.infer_recruiter_company(client, _job()) == ("", "", "")

    def test_llm_error_returns_empty(self):
        class Boom:
            class _M:
                def create(self, **kw):
                    raise RuntimeError("API 挂了")
            messages = _M()
        assert smg.infer_recruiter_company(Boom(), _job()) == ("", "", "")

    def test_missing_company_field_returns_empty(self):
        client = FakeClient(json.dumps({"company": "字节跳动", "confidence": "high",
                                        "evidence": "e"}))
        name, _, _ = smg.infer_recruiter_company(client, _job())
        assert name == "字节跳动"


class TestJudgeHints:
    def test_inferred_hint_marks_source(self):
        hint = smg._big_tech_hint_for(company_inferred=True, proxy_mode=False)
        assert "推断" in hint
        assert "清单" in hint, "仍要保留「清单只是样本」的既有约定"

    def test_proxy_hint_forbids_rejecting_on_missing_info(self):
        hint = smg._big_tech_hint_for(company_inferred=False, proxy_mode=True)
        assert "不要" in hint and "否" in hint, (
            "业务域代理判定必须明确禁止「因信息缺失判否」——这正是 44% 漏判的成因"
        )
        assert "业务" in hint

    def test_default_hint_keeps_existing_wording(self):
        hint = smg._big_tech_hint_for(company_inferred=False, proxy_mode=False)
        assert "不是「有没有命中上面的清单」" in hint, (
            "默认话术必须保留 10-21 去锚定的核心约定"
        )

    def test_proxy_mode_also_keeps_list_is_sample_rule(self):
        hint = smg._big_tech_hint_for(company_inferred=False, proxy_mode=True)
        assert "清单" in hint and "样本" in hint


class TestScoreJobWiring:
    def test_score_job_accepts_inferred_flag(self):
        import inspect
        params = inspect.signature(smg.score_job).parameters
        assert "company_inferred" in params
        assert "proxy_mode" in params

    def test_proxy_mode_reaches_prompt(self):
        """proxy_mode=True 时主 prompt 必须切到业务域代理判定话术。"""
        import inspect
        src = inspect.getsource(smg.score_job)
        assert "proxy_mode" in src
        assert "_big_tech_hint_for" in src


class TestNoHardcodedCompanyNames:
    """用户约束：判断逻辑里不得出现具体公司名。"""

    def _whitelist_names(self):
        import yaml
        cfg = yaml.safe_load(
            (ROOT / "scenarios/boss/config/candidate_profile.yaml").read_text("utf-8")
        )
        wl = (cfg.get("big_tech_whitelist") or {})
        names = list(wl.get("parents") or [])
        for group, brands in (wl.get("subsidiaries") or {}).items():
            names.append(group)
            names.extend(brands)
        return [n for n in names if n]

    def test_prompt_file_has_no_company_names(self):
        prompt_text = (
            ROOT / "scenarios/boss/config/prompts/infer_company.md"
        ).read_text("utf-8")
        for name in self._whitelist_names():
            assert name not in prompt_text, (
                f"推断 prompt 里出现了公司名「{name}」——判断必须由 LLM 做，不能硬编码"
            )

    def test_judge_hints_have_no_company_names(self):
        for kwargs in (
            {"company_inferred": True, "proxy_mode": False},
            {"company_inferred": False, "proxy_mode": True},
            {"company_inferred": False, "proxy_mode": False},
        ):
            hint = smg._big_tech_hint_for(**kwargs)
            for name in self._whitelist_names():
                assert name not in hint, (
                    f"兜底话术里出现了公司名「{name}」（{kwargs}）"
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
