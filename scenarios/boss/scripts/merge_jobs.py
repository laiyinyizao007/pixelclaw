#!/usr/bin/env python3
"""
Boss直聘职位 JSON 合并去重脚本

将同一关键词多次爬取生成的 job_details_*.json 合并为单个无重复文件。

使用方式：
    # 合并 output/ 下所有关键词
    python scenarios/boss/scripts/merge_jobs.py

    # 只合并指定关键词
    python scenarios/boss/scripts/merge_jobs.py --keyword "AI产品经理"

    # 指定输入/输出目录
    python scenarios/boss/scripts/merge_jobs.py --input-dir path/to/output --output-dir path/to/merged
"""

import argparse
import io
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT))

from skills.boss import normalize_card_title

_DEFAULT_OUTPUT = ROOT / "scenarios" / "boss" / "output"
_TIMESTAMP_RE = re.compile(r"_(\d{8}_\d{6})\.json$")


def _job_key(job: dict) -> str:
    li = job.get("list_info", {})
    title = normalize_card_title(li.get("title", "") or "")
    company = li.get("company", "") or ""
    hr_name = li.get("hr_name", "") or ""
    return "\t".join(p for p in [title, company, hr_name] if p)


def _completeness(job: dict) -> tuple:
    """Return (has_description, non_empty_detail_fields, timestamp) for tiebreak."""
    detail = job.get("detail") or {}
    has_desc = bool(detail.get("description", ""))
    non_empty = sum(1 for v in detail.values() if v)
    return (has_desc, non_empty)


def _file_timestamp(path: Path) -> datetime:
    m = _TIMESTAMP_RE.search(path.name)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
        except ValueError:
            pass
    return datetime.min


def _extract_keyword(filename: str) -> str:
    """Extract keyword from job_details_{keyword}_{YYYYMMDD}_{HHMMSS}.json"""
    name = filename.replace("job_details_", "")
    # strip trailing _YYYYMMDD_HHMMSS
    m = _TIMESTAMP_RE.search(filename)
    if m:
        name = name[: name.rfind("_" + m.group(1).split("_")[0])]
    return name


def merge(input_dir: Path, output_dir: Path, keyword: str | None = None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(input_dir.glob("job_details_*.json"))
    if not files:
        print(f"No job_details_*.json found in {input_dir}")
        return

    # group by keyword
    by_kw: dict[str, list[tuple[Path, dict]]] = defaultdict(list)
    for f in files:
        kw = _extract_keyword(f.name)
        if keyword and kw != keyword.replace(" ", "_").replace("/", "-"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            jobs = data.get("jobs", []) if isinstance(data, dict) else data
            for job in jobs:
                by_kw[kw].append((f, job))
        except Exception as e:
            print(f"[warn] skip {f.name}: {e}", file=sys.stderr)

    if not by_kw:
        msg = f"keyword '{keyword}'" if keyword else "any keyword"
        print(f"No records found for {msg}")
        return

    for kw, entries in by_kw.items():
        # deduplicate: keep best record per key
        best: dict[str, tuple[tuple, datetime, dict]] = {}
        for path, job in entries:
            k = _job_key(job)
            if not k:
                continue
            score = _completeness(job)
            ts = _file_timestamp(path)
            if k not in best or score > best[k][0] or (score == best[k][0] and ts > best[k][1]):
                best[k] = (score, ts, job)

        merged_jobs = []
        for idx, (_, _, job) in enumerate(best.values(), start=1):
            entry = dict(job)
            entry["index"] = idx
            merged_jobs.append(entry)

        source_files = sorted({p.name for p, _ in entries})
        out = {
            "keyword": kw,
            "merged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_files": source_files,
            "total_deduplicated": len(merged_jobs),
            "jobs": merged_jobs,
        }

        out_path = output_dir / f"merged_{kw}.json"
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{kw}: {len(entries)} records -> {len(merged_jobs)} unique -> {out_path.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge and deduplicate Boss job JSON files")
    parser.add_argument("--keyword", help="Only merge this keyword (default: all)")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help=f"Directory containing job_details_*.json (default: {_DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for merged_*.json output (default: same as --input-dir)",
    )
    args = parser.parse_args()

    input_dir = args.input_dir
    output_dir = args.output_dir or input_dir
    merge(input_dir, output_dir, keyword=args.keyword)


if __name__ == "__main__":
    main()
