import json

import pytest

from elnino_cta.sources.wind_edb import parse_wind_metric_response


def _stdout(code: str, name: str) -> str:
    payload = {
        "content": [{
            "type": "text",
            "text": json.dumps({
                "metrics": [{
                    "meta": {
                        "code": code,
                        "name": name,
                        "unit": "元/吨",
                        "source": "交易所",
                        "freq": "日",
                        "updateDate": "20260826",
                    },
                    "date": ["20260825", "20260826"],
                    "value": [100.0, 102.0],
                }]
            }, ensure_ascii=False),
        }]
    }
    return json.dumps(payload, ensure_ascii=False)


def test_parse_wind_metric_response_returns_auditable_daily_rows() -> None:
    frame = parse_wind_metric_response(
        _stdout("M0066353", "期货收盘价(活跃合约):棕榈油"),
        expected_code="M0066353",
        expected_name="期货收盘价(活跃合约):棕榈油",
    )

    assert frame["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-08-25", "2026-08-26"]
    assert frame["close"].tolist() == [100.0, 102.0]
    assert frame["indicator_code"].unique().tolist() == ["M0066353"]
    assert frame["source"].unique().tolist() == ["交易所"]


def test_parse_wind_metric_response_rejects_wrong_indicator_even_if_rows_exist() -> None:
    with pytest.raises(ValueError, match="Wind indicator mismatch"):
        parse_wind_metric_response(
            _stdout("M0066366", "期货收盘价(活跃合约):精对苯二甲酸(PTA)"),
            expected_code="M0066364",
            expected_name="期货收盘价(活跃合约):白砂糖",
        )
