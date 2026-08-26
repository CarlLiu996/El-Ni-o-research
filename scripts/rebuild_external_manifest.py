from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from elnino_cta.external_observations import (
    EXTERNAL_FILES,
    _artifact_metadata,
    _latest_status,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="由已落盘外部观测重建审计清单，不重新下载")
    parser.add_argument("--climate-dir", type=Path, required=True)
    parser.add_argument("--as-of", required=True)
    args = parser.parse_args()

    datasets: dict[str, dict[str, object]] = {}
    for dataset, filename in EXTERNAL_FILES.items():
        target = args.climate_dir / filename
        frame = pd.read_csv(target)
        status = _latest_status(dataset, frame)
        datasets[dataset] = _artifact_metadata(dataset, target, status)
    overall = (
        "OK"
        if all(item["status"] in {"OK", "NOT_ANNOUNCED"} for item in datasets.values())
        else "PARTIAL"
    )
    manifest = {
        "overall": overall,
        "as_of": args.as_of,
        "refreshed_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "datasets": datasets,
    }
    target = args.climate_dir / "external_manifest.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    temporary.replace(target)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
