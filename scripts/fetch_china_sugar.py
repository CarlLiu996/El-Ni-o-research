"""Collect public domestic sugar reports independently of Wind."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from elnino_cta.sources.china_sugar import parse_casde, parse_import_report, parse_price_json, parse_province_report


def collect(repo: Path, sources: list[dict], *, as_of: str, offline: bool = False) -> dict:
    raw_dir = repo / "data/raw/china_sugar"
    output = repo / "data/processed/china_sugar"
    raw_dir.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"as_of": as_of, "refreshed_at": datetime.now(timezone.utc).isoformat(), "sources": {},
                "boundary": "省级实际与全国年度估计/预测分开；不自动进入CTA配置或历史置信度修正"}
    buckets: dict[str, list[pd.DataFrame]] = {key: [] for key in ("province_history", "casde_history", "price_history", "imports")}
    for item in sources:
        suffix = ".json" if item["kind"] in {"price", "import_cost"} else ".html"
        cache = raw_dir / (item["id"] + suffix)
        meta = {"kind": item["kind"], "source_url": item["url"], "region": item.get("region", "全国"),
                "rows": 0, "status": "FETCH_ERROR", "reason": "", "raw_path": str(cache.relative_to(repo))}
        content = None
        try:
            if offline:
                content = cache.read_bytes()
                meta["retrieval"] = "离线缓存；未声称本次联网刷新"
            else:
                response = requests.get(item["url"], timeout=25)
                response.raise_for_status()
                content = response.content
                meta["retrieval"] = "本次联网下载"
            meta["sha256"] = hashlib.sha256(content).hexdigest()
            if item["kind"] == "province":
                frame = parse_province_report(content, region=item["region"], observation_date=item["date"], source_url=item["url"])
                key = "province_history"
            elif item["kind"] == "casde":
                frame = parse_casde(content, source_url=item["url"])
                key = "casde_history"
            elif item["kind"] == "price":
                frame = parse_price_json(json.loads(content))
                frame["source_url"] = item["url"]
                key = "price_history"
            elif item["kind"] == "import":
                frame = parse_import_report(content, observation_date=item["date"], source_url=item["url"])
                key = "imports"
            else:
                payload = json.loads(content)
                dates = pd.to_datetime(payload["category"], format="mixed", errors="raise")
                meta.update(rows=len(dates), data_as_of=dates.max().strftime("%Y-%m-%d"), status="STALE",
                            reason="公开接口已滞后；不得用于当前进口成本或利润判断")
                if not offline:
                    cache.write_bytes(content)
                manifest["sources"][item["id"]] = meta
                continue
            if "published_at" in frame:
                frame = frame[pd.to_datetime(frame.published_at).le(pd.Timestamp(as_of))]
            else:
                frame = frame[pd.to_datetime(frame.date).le(pd.Timestamp(as_of))]
            if frame.empty:
                raise ValueError("报告日在本次观察日之后或没有有效数据")
            if not offline:
                cache.write_bytes(content)
            frame["source_id"] = item["id"]
            buckets[key].append(frame)
            meta.update(rows=len(frame), status="REVIEW_REQUIRED" if item["kind"] == "import" else "PARTIAL" if item["kind"] == "province" else "OK",
                        data_as_of=str(frame.date.max())[:10], published_at=str(frame.published_at.max()) if "published_at" in frame else "未提供逐日发布版本",
                        reason="省级近月实际，仅覆盖广西/云南；不能代替全国" if item["kind"] == "province" else
                        "年度估计与预测，仅作背景；结余变化不是期末库存" if item["kind"] == "casde" else
                        "转载近似数，未取得海关原始明细，不评分" if item["kind"] == "import" else "现货/原糖指数，不是可交易连续期货；无历史发布版本")
        except (requests.RequestException, ValueError, KeyError, OSError) as exc:
            meta.update(status="PARSE_ERROR" if content is not None else "FETCH_ERROR", reason=f"{type(exc).__name__}: {exc}")
        manifest["sources"][item["id"]] = meta
    for key, frames in buckets.items():
        # Never retain a previous successful table under a newly failed manifest.
        frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        frame.to_csv(output / f"{key}.csv", index=False, encoding="utf-8-sig")
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    sources = json.loads((args.repo / "config/china_sugar_sources.json").read_text(encoding="utf-8"))
    manifest = collect(args.repo, sources, as_of=args.as_of, offline=args.offline)
    print(json.dumps({key: {field: value.get(field) for field in ("status", "rows", "reason")} for key, value in manifest["sources"].items()}, ensure_ascii=False, indent=2))
    if any(item["status"] in {"FETCH_ERROR", "PARSE_ERROR"} for item in manifest["sources"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
