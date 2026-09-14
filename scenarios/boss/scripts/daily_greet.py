#!/usr/bin/env python3
"""
daily_greet.py — 每日量身定制打招呼全流程编排

按顺序执行三步：
  Step 1: scrape_job_details.py   爬取新职位（所有关键词）
  Step 2: analyze_requirements.py 分析职位需求（LLM 提取标签）
  Step 3: smart_match_greet.py    评分 + 发送个性化打招呼

使用方式：
  # 仅评分（调试，不发送）
  python scenarios/boss/scripts/daily_greet.py --score-only

  # 正式运行（阈值 7，严格模式）
  python scenarios/boss/scripts/daily_greet.py --threshold 7 --strict

  # 跳过爬取（数据已存在）
  python scenarios/boss/scripts/daily_greet.py --skip-scrape --threshold 7 --strict
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).parents[3]
SCRIPTS_DIR = Path(__file__).parent
PYTHON = sys.executable


def run(cmd: list[str], step_label: str) -> bool:
    print(f"\n{'='*60}")
    print(f"  {step_label}")
    print(f"{'='*60}")
    print(f"  $ {' '.join(cmd)}\n")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(cmd, env=env)
    if proc.returncode != 0:
        print(f"\n✗ {step_label} 退出码 {proc.returncode}，中止流程")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Boss直聘每日量身打招呼全流程")
    parser.add_argument("--threshold", type=int, default=7, help="最低匹配分数（默认 7）")
    parser.add_argument("--max-greet", type=int, default=None,
                        help="最多发送打招呼数（省略则不设上限，由平台弹窗终止）")
    parser.add_argument("--strict", action="store_true", help="严格评分模式")
    parser.add_argument("--score-only", action="store_true", help="仅评分不发送（调试用）")
    parser.add_argument("--min-salary", type=float, default=0, help="月薪下限（K）")
    parser.add_argument("--skip-scrape", action="store_true", help="跳过爬取步骤")
    parser.add_argument("--skip-analyze", action="store_true", help="跳过需求分析步骤")
    parser.add_argument("--keyword", default=None,
                        help="仅处理指定关键词（省略则使用 keywords.yaml 所有关键词）")
    parser.add_argument("--device", default=None, help="ADB 设备 serial")
    args = parser.parse_args()

    # ── Step 1: 爬取 ────────────────────────────────────────────────────────────
    if not args.skip_scrape:
        scrape_cmd = [PYTHON, str(SCRIPTS_DIR / "scrape_job_details.py")]
        if args.keyword:
            scrape_cmd += ["--keyword", args.keyword]
        if args.device:
            scrape_cmd += ["--device", args.device]
        if not run(scrape_cmd, "Step 1/3  爬取新职位"):
            return 1

    # ── Step 2: 分析 ────────────────────────────────────────────────────────────
    if not args.skip_analyze:
        analyze_cmd = [PYTHON, str(SCRIPTS_DIR / "analyze_requirements.py")]
        if args.keyword:
            analyze_cmd += ["--keyword", args.keyword]
        if not run(analyze_cmd, "Step 2/3  分析职位需求"):
            return 1

    # ── Step 3: 评分 + 打招呼 ───────────────────────────────────────────────────
    # Determine keywords to process
    if args.keyword:
        keywords = [args.keyword]
    else:
        import yaml
        config_path = REPO_ROOT / "scenarios" / "boss" / "config" / "keywords.yaml"
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        keywords = [str(k) for k in cfg.get("keywords", [])]

    errors = 0
    for kw in keywords:
        greet_cmd = [
            PYTHON, str(SCRIPTS_DIR / "smart_match_greet.py"),
            "--keyword", kw,
            "--threshold", str(args.threshold),
        ]
        if args.max_greet is not None:
            greet_cmd += ["--max-greet", str(args.max_greet)]
        if args.strict:
            greet_cmd.append("--strict")
        if args.score_only:
            greet_cmd.append("--score-only")
        if args.min_salary:
            greet_cmd += ["--min-salary", str(args.min_salary)]
        if args.device:
            greet_cmd += ["--device", args.device]

        label = f"Step 3/3  评分+打招呼「{kw}」"
        if not run(greet_cmd, label):
            errors += 1

    if errors:
        print(f"\n✗ {errors} 个关键词处理失败")
        return 1

    print("\n✓ 全流程完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
