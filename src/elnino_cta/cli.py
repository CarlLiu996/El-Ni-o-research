from __future__ import annotations

import argparse
import json
from pathlib import Path

from .debrief import write_debrief
from .external_observations import refresh_external_observations
from .monitoring import build_snapshot, write_snapshot
from .pipeline import refresh, refresh_history


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh El Nino agricultural CTA research data")
    sub = parser.add_subparsers(dest="command", required=True)
    refresh_parser = sub.add_parser("refresh", help="Fetch climate/futures data and build CTA proxies")
    refresh_parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    refresh_parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    refresh_parser.add_argument("--output", default="data/processed")
    refresh_parser.add_argument("--regions", default="config/regions.json")
    refresh_parser.add_argument(
        "--futures-provider", choices=["auto", "tushare", "sina"], default="auto"
    )
    monitor_parser = sub.add_parser(
        "monitor", help="Evaluate freshness, climate, fundamentals, market structure, and CTA state"
    )
    monitor_parser.add_argument("--data", default="data/processed")
    monitor_parser.add_argument("--config", default="config/monitoring.json")
    monitor_parser.add_argument("--output", default="reports/monitoring")
    monitor_parser.add_argument("--as-of", help="YYYY-MM-DD; defaults to today")
    monitor_parser.add_argument(
        "--fail-on-critical", action="store_true", help="Exit non-zero when critical alerts exist"
    )

    debrief_parser = sub.add_parser("debrief", help="Render a Markdown review from latest monitor JSON")
    debrief_parser.add_argument("--snapshot", default="reports/monitoring/latest.json")
    debrief_parser.add_argument("--output", default="reports/monitoring")
    external_parser = sub.add_parser(
        "external-refresh", help="Refresh India monsoon and NDVI observations"
    )
    external_parser.add_argument("--output", default="data/processed")
    external_parser.add_argument("--as-of", required=True)
    external_parser.add_argument("--regions", default="config/regions.json")
    external_parser.add_argument("--normals", default="config/india_monsoon_normals.json")
    external_parser.add_argument("--ndvi-start", default="2001-01-01")
    history_parser = sub.add_parser(
        "history-refresh", help="Build independent ENSO history review data"
    )
    history_parser.add_argument("--output", default="data/processed")
    history_parser.add_argument("--as-of", required=True)
    history_parser.add_argument("--regions", default="config/regions.json")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "refresh":
        manifest = refresh(
            Path(args.output), args.start, args.end, Path(args.regions), args.futures_provider
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
    elif args.command == "monitor":
        snapshot = build_snapshot(Path(args.data), Path(args.config), args.as_of)
        latest_path, history_path = write_snapshot(snapshot, Path(args.output))
        debrief_path = write_debrief(snapshot, Path(args.output))
        print(json.dumps({
            "status": snapshot["research_gate"]["overall"],
            "critical_alerts": snapshot["critical_alerts"],
            "warning_alerts": snapshot["warning_alerts"],
            "latest": str(latest_path),
            "history": str(history_path),
            "debrief": str(debrief_path),
        }, ensure_ascii=False, indent=2))
        if args.fail_on_critical and snapshot["critical_alerts"]:
            raise SystemExit(2)
    elif args.command == "debrief":
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
        path = write_debrief(snapshot, Path(args.output))
        print(path)
    elif args.command == "external-refresh":
        manifest = refresh_external_observations(
            output_dir=Path(args.output),
            as_of=args.as_of,
            regions_path=Path(args.regions),
            normals_path=Path(args.normals),
            ndvi_start=args.ndvi_start,
            source_bundle=None,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
    elif args.command == "history-refresh":
        manifest = refresh_history(
            output_dir=Path(args.output),
            as_of=args.as_of,
            regions_path=Path(args.regions),
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
