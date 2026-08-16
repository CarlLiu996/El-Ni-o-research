from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import refresh


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
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "refresh":
        manifest = refresh(
            Path(args.output), args.start, args.end, Path(args.regions), args.futures_provider
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
