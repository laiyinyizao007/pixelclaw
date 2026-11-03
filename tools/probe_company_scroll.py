#!/usr/bin/env python3
"""真机探针：量一下「详情页公司名」在滚动前后的差异（2026-10-23）。

目的：验证 `BOSSAutomationSkill.scroll_for_company_info()` 真的能在真机上把
公司信息块抓出来——即「滚到底」这条修法对不对、值不值。

对每个岗位记录三列：
  - 列表卡片公司名（RecyclerView 懒加载绑定，常空）
  - 详情页公司名（滚动前）
  - 详情页公司名（滚动后 = 修复后的取值）

不发招呼、不调 LLM，纯读。

用法：
    PYTHONUTF8=1 python tools/probe_company_scroll.py --keyword "AI产品经理" -n 8
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from skills.android.adb_runner import ADBRunner  # noqa: E402
from skills.boss import BOSSAutomationSkill  # noqa: E402
from scenarios.boss.scripts.smart_match_greet import _try_auto_unlock  # noqa: E402
from utils.device_lock import device_lock  # noqa: E402
from utils.logging_setup import setup_logger  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", default="AI产品经理")
    ap.add_argument("-n", "--jobs", type=int, default=8)
    ap.add_argument("--device", default=None)
    args = ap.parse_args()

    logger = setup_logger("probe_company_scroll", log_dir="./logs/boss")

    with device_lock(script_name=f"probe_company_scroll:{args.keyword}"):
        adb = ADBRunner()
        skill = BOSSAutomationSkill(adb, device_id=args.device)

        # 屏幕常亮 + 解锁
        skill._adb("shell svc power stayon true")
        skill._adb("shell settings put system screen_off_timeout 1800000")
        if not skill.ensure_ready():
            logger.warning("ensure_ready 失败，尝试解锁")
            _try_auto_unlock(skill)
        skill.launch()
        time.sleep(2.0)

        # 上一次跑完可能停在聊天页/搜索页——先回到能搜索的状态
        searched = False
        for _ in range(5):
            if skill.find_element(resource_id="com.hpbr.bosszhipin:id/et_search"):
                # 已经在搜索输入浮层里，直接输关键词提交
                searched = skill._submit_search(args.keyword)
                break
            if skill.is_on_job_list() and skill.browse_jobs(args.keyword):
                searched = True
                break
            skill.press_back()
            time.sleep(1.2)

        if not searched:
            print(f"❌ 搜索「{args.keyword}」失败")
            return 1
        time.sleep(2.0)

        jobs = skill.get_job_list()
        print(f"列表共 {len(jobs)} 个岗位，探测前 {args.jobs} 个\n")
        print(f"{'#':>2} {'卡片公司':<14} {'详情(展开后)':<22} {'详情(滚底后)':<22} {'轮':>3}  标题")
        print("-" * 108)

        rows = []
        for i, job in enumerate(jobs[: args.jobs], 1):
            if not skill.navigate_to_job(job):
                print(f"{i:>2} 导航失败：{job.title[:20]}")
                continue
            if not skill.wait_for_element(
                    "com.hpbr.bosszhipin:id/tv_job_name", timeout=6.0):
                print(f"{i:>2} 详情页加载超时：{job.title[:20]}")
                continue

            # ① 详情页顶部（展开描述之前）读一次——公司块在底部，这时必然读不到
            top = skill.get_job_detail()
            top_company = (top.get("company") or "").strip()

            # ② 展开描述（会顺带小幅滚动）后读一次——**这是旧代码的取值点**
            skill.expand_description()
            detail = skill.get_job_detail()
            before = (detail.get("company") or "").strip()

            # ③ 新增：滚到底抓公司信息块
            scrolls = 0
            after = before
            if not before:
                _orig_scroll = skill.scroll_down
                _counter = {"n": 0}

                def _counting_scroll(*a, **kw):
                    _counter["n"] += 1
                    return _orig_scroll(*a, **kw)

                skill.scroll_down = _counting_scroll
                try:
                    detail = skill.scroll_for_company_info(detail)
                finally:
                    skill.scroll_down = _orig_scroll
                after = (detail.get("company") or "").strip()
                scrolls = _counter["n"]

            print(f"{i:>2} {job.company or '—':<14} {before or '—':<22} "
                  f"{after or '—':<22} {scrolls:>3}  {job.title[:22]}")
            rows.append({
                "card": job.company or "",
                "top": top_company,
                "before": before,
                "after": after,
                "recovered": bool(after) and not before,
                "scrolls": scrolls,
            })
            skill.press_back()
            time.sleep(1.2)

        if not rows:
            print("\n没有采到样本")
            return 1

        n = len(rows)
        card_ok = sum(1 for r in rows if r["card"])
        top_ok = sum(1 for r in rows if r["top"])
        before_ok = sum(1 for r in rows if r["before"])
        after_ok = sum(1 for r in rows if r["after"])
        recovered = sum(1 for r in rows if r["recovered"])
        scrolls = [r["scrolls"] for r in rows]
        print("=" * 108)
        print(f"样本 {n} 个")
        print(f"  列表卡片有公司名        : {card_ok}/{n}")
        print(f"  详情页顶部有公司名      : {top_ok}/{n}   （底部块还没 inflate，必然空）")
        print(f"  详情页(展开描述后)有    : {before_ok}/{n}   ← **旧代码的取值点**")
        print(f"  详情页(滚底后)有        : {after_ok}/{n}   ← **修复后的取值点**")
        print(f"  滚底新救回来的          : {recovered}/{n}")
        if scrolls:
            print(f"  滚底轮数                : 最多 {max(scrolls)}，平均 {sum(scrolls)/len(scrolls):.1f}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
