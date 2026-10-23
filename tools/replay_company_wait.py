#!/usr/bin/env python3
"""回放：把 10-22「因公司主体缺失被判非大厂」的岗位，用**孪生记录里的真实公司名**
重新跑一次 LLM 判定，量化「等公司块渲染出来」这条修复能挽回多少。

背景：空主体的主因是提取过早（详情页公司块是独立异步请求），修复方式是
`wait_for_detail_company()` 先等一轮再评分。本脚本**离线**估算这次等待成功
之后会发生什么：把 DB 里同 title+hr_name 的孪生记录的公司名填回去，重跑判定。

用法：
    PYTHONUTF8=1 python tools/replay_company_wait.py
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scenarios.boss.scripts import smart_match_greet as smg  # noqa: E402

DB = ROOT / "scenarios/boss/output/requirements.db"

SQL = """
SELECT a.id, a.title, a.hr_name, a.salary, a.experience, a.description,
       a.score AS anon_score, a.skip_reason,
       b.company AS twin_company, b.score AS twin_score
FROM job_visits a
JOIN job_visits b
  ON b.title = a.title AND b.hr_name = a.hr_name
 AND b.company <> '' AND b.id <> a.id AND b.visited_at >= '2026-10-22'
WHERE a.company = '' AND a.company_info = ''
  AND a.visited_at >= '2026-10-22'
  AND a.skip_reason LIKE 'LLM判非大厂%'
"""


def main() -> int:
    resume = (Path.home() / "Projects/resume-renew/resume/current.md").read_text("utf-8")
    profile = smg._load_candidate_profile()
    whitelist = profile.get("big_tech_whitelist") or {}
    dims = profile.get("scoring_dimensions") or {}
    primary, fallback = smg._make_client()

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(SQL).fetchall()
    print(f"待回放：{len(rows)} 条（因主体缺失被判非大厂、且有孪生记录）\n")

    hit, miss = [], []
    for i, r in enumerate(rows, 1):
        job = {
            "title": r["title"],
            "company": r["twin_company"],       # ← 唯一变化：填上真实公司名
            "company_info": "",
            "salary_raw": r["salary"] or "未知",
            "experience": r["experience"] or "未知",
            "hr_name": r["hr_name"] or "（未知）",
            "description": r["description"] or "",
            "requirements_text": "  无标签（请从JD描述中判断）",
        }
        try:
            scored = smg.score_job(primary, resume, job, strict=True,
                                   fallback=fallback, scoring_dimensions=dims,
                                   big_tech_whitelist=whitelist)
        except Exception as exc:  # noqa: BLE001
            print(f"[{i}/{len(rows)}] {r['title'][:26]} → LLM 失败：{exc}")
            continue
        ok = bool(scored.get("is_big_tech"))
        line = (f"[{i}/{len(rows)}] {r['twin_company']:<12} score={scored.get('match_score')}"
                f" big_tech={ok} | {r['title'][:24]}")
        print(line)
        (hit if ok else miss).append((r, scored))

    print("\n" + "=" * 60)
    print(f"公司名补齐后判为大厂：{len(hit)} / {len(rows)}"
          f"（{100.0*len(hit)/max(len(rows),1):.0f}%）")
    print(f"仍判非大厂：{len(miss)} 条 —— "
          f"{', '.join(sorted({r['twin_company'] for r, _ in miss}))}")
    n_send = sum(1 for _, s in hit if (s.get("match_score") or 0) >= 7)
    print(f"其中 score ≥ 7 会真发招呼：{n_send} 条")
    print("\n对照：这些岗位在 10-22 因主体缺失被拒发 0 条；"
          "两级兜底（反推+代理）回放挽回 28/43。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
