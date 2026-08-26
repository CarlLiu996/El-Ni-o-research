from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from elnino_cta.signals import build_cta_proxy


def main() -> None:
    parser = argparse.ArgumentParser(
        description="用 Wind 活跃合约生成仅供最新方向复核的 CTA 代理"
    )
    parser.add_argument("--futures-dir", type=Path, required=True)
    parser.add_argument("--signals-dir", type=Path, required=True)
    args = parser.parse_args()
    args.signals_dir.mkdir(parents=True, exist_ok=True)

    artifacts: dict[str, object] = {}
    for symbol in ("P", "SR"):
        history = pd.read_csv(args.futures_dir / f"{symbol}_active_wind.csv")
        for speed in ("fast", "medium", "slow"):
            signal = build_cta_proxy(history, speed)
            signal["provider"] = "Wind EDB active contract"
            signal["research_status"] = "EXPLORATORY_ROLL_METHOD_UNDISCLOSED"
            target = args.signals_dir / f"{symbol}_{speed}_proxy_wind_exploratory.csv"
            temporary = target.with_suffix(".tmp")
            signal.to_csv(temporary, index=False)
            temporary.replace(target)
            latest = signal.iloc[-1]
            artifacts[f"{symbol}_{speed}"] = {
                "file": target.name,
                "date": str(latest["date"])[:10],
                "target_position": float(latest["target_position"]),
                "executed_position": float(latest["position"]),
            }

    manifest = {
        "status": "EXPLORATORY",
        "boundary": "仅用于最新方向复核；Wind活跃合约换月/复权规则未披露，不进入正式CTA确认或历史回测",
        "artifacts": artifacts,
    }
    (args.signals_dir / "wind_exploratory_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
