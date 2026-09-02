from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import pandas as pd

from elnino_cta.sources.wind_edb import parse_wind_metric_response


INDICATORS = {
    "P": ("M0066353", "期货收盘价(活跃合约):棕榈油"),
    "SR": ("M0066364", "期货收盘价(活跃合约):白砂糖"),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_one(
    *,
    node: Path,
    cli_dir: Path,
    code: str,
    name: str,
    begin: str,
    end: str,
) -> pd.DataFrame:
    request = {
        "question": f"仅提取指标代码{code}，指标名称为{name}；不要返回其他指标",
        "beginDate": begin,
        "endDate": end,
    }
    request_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", prefix="wind-edb-", encoding="utf-8", delete=False
        ) as handle:
            json.dump(request, handle, ensure_ascii=False)
            request_path = Path(handle.name)
        completed = subprocess.run(
            [
                str(node),
                "scripts/cli.mjs",
                "call",
                "economic_data",
                "query_economic_indicator_data",
                f"@{request_path}",
            ],
            cwd=cli_dir,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return parse_wind_metric_response(
            completed.stdout, expected_code=code, expected_name=name
        )
    finally:
        if request_path is not None:
            request_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="刷新 Wind 棕榈油/白糖活跃合约收盘价")
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--cli-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--begin", default="2020-01-01")
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    refreshed_at = pd.Timestamp.now(tz="UTC").isoformat()
    artifacts: dict[str, object] = {}
    for symbol, (code, name) in INDICATORS.items():
        frame = fetch_one(
            node=args.node,
            cli_dir=args.cli_dir,
            code=code,
            name=name,
            begin=args.begin,
            end=args.end,
        )
        frame["downloaded_at"] = refreshed_at
        target = args.output_dir / f"{symbol}_active_wind.csv"
        temporary = target.with_suffix(".tmp")
        frame.to_csv(temporary, index=False)
        temporary.replace(target)
        artifacts[symbol] = {
            "file": target.name,
            "rows": len(frame),
            "min_date": frame["date"].min().date().isoformat(),
            "max_date": frame["date"].max().date().isoformat(),
            "indicator_code": code,
            "indicator_name": name,
            "source": str(frame["source"].iloc[-1]),
            "provider": "Wind EDB",
            "sha256": _sha256(target),
        }

    manifest = {
        "status": "OK",
        "refreshed_at": refreshed_at,
        "boundary": "活跃合约连续行情用于L3定价展示；不等同于可审计换月的CTA回测序列",
        "artifacts": artifacts,
    }
    manifest_path = args.output_dir / "wind_futures_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
