#!/usr/bin/env python3
"""
sweep_big_tech.py — 大厂定向 sweep：逐个公司名搜索，累计发够 N 次招呼。

为什么需要它
------------
`smart_match_greet.py --only-big-tech` 是「一个关键词跑一轮」的结构。
用**岗位词**搜索（"AI产品经理"）时，命中大厂的比例极低（实测 12 个职位只中 1 个，
约 8%），120 次要烧掉十几个小时的 LLM 配额。

用**公司名**搜索（"字节跳动"）时，Boss 返回的是该公司在招的职位，且排序把
AI/产品岗顶到前面——实测 16 张卡里 13 张是 AI 产品经理 / AI 产品专家 / 设计专家。
于是「按公司名逐个扫」把命中率从 8% 拉到接近 100%。

一个进程只搜一个关键词：Boss 搜索框不支持多词组合（"字节跳动 AI" 搜不出东西），
且连续两次搜索时 `_open_search_from_job_list()` 有概率找不到搜索入口。
所以这里每个关键词起一个独立子进程。

用法
----
    # 先看关键词计划（不发任何招呼）
    python scenarios/boss/scripts/sweep_big_tech.py --list-only

    # 累计发 30 次（每个公司最多 8 次）
    python scenarios/boss/scripts/sweep_big_tech.py --target 30

    # 指定设备 + 每次多发点
    python scenarios/boss/scripts/sweep_big_tech.py --target 120 --per-company 10 \
        --device 10.32.7.135:5555

去重由 `smart_match_greet.py` 的 greetings / job_visits 表负责，
同一个岗位换个关键词再搜到也不会重复打招呼。
"""
import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parents[3]
SMART_MATCH = REPO_ROOT / "scenarios" / "boss" / "scripts" / "smart_match_greet.py"
PROFILE = REPO_ROOT / "scenarios" / "boss" / "config" / "candidate_profile.yaml"

# 这些词搜不出有效职位（或会搜到一堆同名噪音），跳过
_SKIP_KEYWORDS = {"1688", "UC", "QQ", "Riot", "POCO", "Redmi", "黑鲨"}


def _has_cjk(text: str) -> bool:
    """Boss 是中文站，纯英文/拼音的词条搜不出对应公司。"""
    return any("一" <= ch <= "鿿" for ch in text)


def load_keywords(profile_path: Path = PROFILE) -> list[str]:
    """从 candidate_profile.yaml 的 big_tech_whitelist 段生成搜索关键词。

    顺序 = yaml 里的书写顺序（互联网头部 → AI 新势力 → 半导体/自动驾驶 →
    外企 → 准大厂），先跑命中率高的大厂。

    母公司 + 旗下品牌都算大厂（用户 2026-10-20 确认），所以 subsidiaries
    的品牌名也进搜索列表。
    """
    import yaml

    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    whitelist = profile.get("big_tech_whitelist") or {}

    keywords: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        name = (name or "").strip()
        if not name or name in seen or name in _SKIP_KEYWORDS:
            return
        if not _has_cjk(name):
            return
        seen.add(name)
        keywords.append(name)

    parents = [p for p in (whitelist.get("parents") or []) if p]
    # 母公司之间的「短名是长名前缀」去重（阿里/阿里巴巴、字节/字节跳动、蚂蚁/蚂蚁集团）。
    # 只在 parents 内部做——跨组去重会把「百度」误删（它是「百度网盘」的前缀）。
    redundant = {
        p for p in parents
        if len(p) <= 4 and any(p != q and q.startswith(p) for q in parents)
    }
    for parent in parents:
        if parent in redundant:
            continue
        _add(parent)
    for brands in (whitelist.get("subsidiaries") or {}).values():
        for brand in brands or []:
            _add(brand)
    return keywords


_RE_SENT = re.compile(r"打招呼成功：(\d+)\s*条")


def run_one(
    keyword: str,
    max_greet: int,
    device: str | None,
    threshold: float,
    min_salary: int,
    timeout: int,
) -> tuple[int, str, int]:
    """跑一个关键词，返回 (发出条数, 输出文本, 退出码)。"""
    cmd = [
        sys.executable, str(SMART_MATCH),
        "--keyword", keyword,
        "--only-big-tech",
        "--max-greet", str(max_greet),
        "--threshold", str(threshold),
        "--strict",
        "--min-salary", str(min_salary),
    ]
    if device:
        cmd += ["--device", device]
        # 断连自动重连要指向同一台设备，否则重连到别的 IP 上
        cmd += ["--adb-reconnect-ip", device]
    try:
        proc = subprocess.run(
            cmd, cwd=REPO_ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return _parse_sent(proc.stdout or ""), proc.stdout or "", proc.returncode
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return _parse_sent(out), out, -1


def _parse_sent(output: str) -> int:
    match = _RE_SENT.search(output)
    return int(match.group(1)) if match else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="大厂定向 sweep 打招呼")
    parser.add_argument("--target", type=int, default=30, help="累计发送目标条数")
    parser.add_argument("--per-company", type=int, default=8, help="单个公司最多发多少条")
    parser.add_argument("--device", default=None, help="ADB 设备 serial（如 10.32.7.135:5555）")
    parser.add_argument("--threshold", type=float, default=7, help="最低匹配分数")
    parser.add_argument("--min-salary", type=int, default=18, help="月薪下限（K）")
    parser.add_argument("--timeout", type=int, default=3600, help="单个关键词超时秒数")
    parser.add_argument("--logdir", default="/tmp/sweep_logs", help="日志目录")
    parser.add_argument("--keywords", default=None, help="逗号分隔的自定义关键词（覆盖 yaml）")
    parser.add_argument("--list-only", action="store_true", help="只打印关键词计划，不发送")
    args = parser.parse_args()

    if args.keywords:
        keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    else:
        keywords = load_keywords()

    if args.list_only:
        print(f"共 {len(keywords)} 个关键词（顺序即执行顺序）：")
        for i, kw in enumerate(keywords, 1):
            print(f"  {i:3d}. {kw}")
        return 0

    logdir = Path(args.logdir)
    logdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    master = logdir / f"sweep_{stamp}.log"

    sent = 0
    summary: list[tuple[str, int, int]] = []
    with master.open("w", encoding="utf-8") as mf:
        mf.write(f"# sweep target={args.target} per_company={args.per_company} start={stamp}\n")
        for i, keyword in enumerate(keywords, 1):
            if sent >= args.target:
                break
            quota = min(args.per_company, args.target - sent)
            mf.write(f"\n\n########## [{i}/{len(keywords)}] {keyword} (max-greet={quota}) ##########\n")
            mf.flush()
            started = time.time()
            got, output, code = run_one(
                keyword, quota, args.device, args.threshold, args.min_salary, args.timeout,
            )
            elapsed = int(time.time() - started)
            sent += got
            summary.append((keyword, got, elapsed))
            mf.write(f"---- exit={code} sent={got} cumulative={sent} elapsed={elapsed}s\n")
            mf.flush()
            (logdir / f"{stamp}_{i:03d}_{keyword}.out").write_text(output, encoding="utf-8")
            print(f"[{i}/{len(keywords)}] {keyword}: +{got} (累计 {sent}/{args.target}) {elapsed}s",
                  flush=True)

    print("\n===== sweep 汇总 =====")
    for keyword, got, elapsed in summary:
        print(f"  {keyword}: {got} 条 / {elapsed}s")
    print(f"总计发出 {sent} 条（目标 {args.target}）")
    print(f"日志：{master}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
