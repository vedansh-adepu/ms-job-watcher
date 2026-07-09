#!/usr/bin/env python3
"""Union view across all per-pipeline emailed job logs.

Usage:
  python scripts/show_emailed.py              # full history
  python scripts/show_emailed.py --since 7    # last N days
  python scripts/show_emailed.py --bucket yes # yes | maybe
  python scripts/show_emailed.py --pipeline boards3
"""
import argparse, glob, json, sys
from collections import Counter
from datetime import datetime, timezone, timedelta


def load_all():
    records = []
    for path in sorted(glob.glob("state/emailed_*.json")):
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, list):
                records.extend(data)
        except Exception as e:
            print(f"[WARN] Could not load {path}: {e}", file=sys.stderr)
    return sorted(records, key=lambda r: r.get("emailed_utc", ""))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", type=int, metavar="DAYS", help="Only show records from the last N days")
    parser.add_argument("--bucket", choices=["yes", "maybe"], help="Filter by classification bucket")
    parser.add_argument("--pipeline", help="Filter by pipeline name (main, boards, boards2, boards3)")
    parser.add_argument("--tail", type=int, default=20, metavar="N", help="How many recent records to show (default 20)")
    args = parser.parse_args()

    records = load_all()
    if not records:
        print("No emailed records found. Records appear once PIPELINE_NAME is set and a job is emailed.")
        return

    if args.since:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=args.since)).isoformat()
        records = [r for r in records if r.get("emailed_utc", "") >= cutoff]
    if args.bucket:
        records = [r for r in records if r.get("bucket") == args.bucket]
    if args.pipeline:
        records = [r for r in records if r.get("pipeline") == args.pipeline]

    if not records:
        print("No records match the given filters.")
        return

    print(f"Total emailed: {len(records)}")
    print(f"Date range:    {records[0]['emailed_utc'][:10]} → {records[-1]['emailed_utc'][:10]}")
    print()

    by_pipeline = Counter(r.get("pipeline", "?") for r in records)
    print("By pipeline:")
    for p, n in sorted(by_pipeline.items()):
        print(f"  {p:<14}  {n:>4}")
    print()

    by_bucket = Counter(r.get("bucket", "?") for r in records)
    print("By bucket:")
    for b, n in sorted(by_bucket.items()):
        print(f"  {b:<8}  {n:>4}")
    print()

    by_plat = Counter(r.get("platform", "?") for r in records)
    print("By platform (top 10):")
    for plat, n in by_plat.most_common(10):
        print(f"  {plat:<22}  {n:>4}")
    print()

    tail = records[-args.tail:]
    print(f"Most recent {len(tail)} record(s):")
    for r in tail:
        ts = r.get("emailed_utc", "")[:16]
        bucket = r.get("bucket", "?").upper()
        pipe = r.get("pipeline", "?")
        co = r.get("company", "?")
        title = r.get("title", "?")
        loc = r.get("location", "")
        print(f"  [{ts}] [{bucket:<5}] [{pipe}] {co} — {title}")
        if loc:
            print(f"    {loc}")
        print(f"    {r.get('url', '')}")


if __name__ == "__main__":
    main()
