import pandas as pd
import pytest

from elnino_cta.expanded_review import build_focal_event_matrix, summarize_focal_patterns


def test_focal_matrix_aggregates_multiple_price_proxies_to_one_chain_row():
    events = pd.DataFrame({
        "event_id": ["1997-1998"],
        "peak_oni": [2.37],
        "status": ["COMPLETED"],
    })
    evidence = pd.DataFrame([
        {
            "event_id": "1997-1998", "commodity": "soybean",
            "metric_id": "weather:brazil_soybean:precip_anomaly_pct", "value": -0.10,
        },
        {
            "event_id": "1997-1998", "commodity": "soybean",
            "metric_id": "crop:Brazil:Soya beans:Yield:y0:deviation_vs_prior5_median",
            "value": -0.05,
        },
    ])
    price_rows = []
    for proxy, horizon_value in (
        ("soybeans", 110.0), ("soybean_meal", 120.0), ("soybean_oil", 130.0)
    ):
        price_rows.extend([
            {
                "event_id": "1997-1998", "status": "COMPLETED",
                "commodity": proxy, "relative_month": 0, "price_index": 100.0,
            },
            {
                "event_id": "1997-1998", "status": "COMPLETED",
                "commodity": proxy, "relative_month": 6, "price_index": horizon_value,
            },
        ])
    cta = pd.DataFrame([
        {
            "event_id": "1997-1998", "status": "COMPLETED",
            "commodity": proxy, "speed": "medium", "horizon_months": 6,
            "net_return": value,
        }
        for proxy, value in (
            ("soybeans", 0.03), ("soybean_meal", 0.06), ("soybean_oil", 0.09)
        )
    ])

    matrix = build_focal_event_matrix(
        events, evidence, pd.DataFrame(price_rows), cta, focal_event_ids=("1997-1998",)
    )

    soybean = matrix.loc[matrix["chain_id"].eq("soybean")].iloc[0]
    assert len(matrix) == 6
    assert soybean["price_6m"] == pytest.approx(0.20)
    assert soybean["cta_medium_6m"] == pytest.approx(0.06)
    assert soybean["weather_precip_anomaly"] == pytest.approx(-0.10)
    assert soybean["yield_deviation"] == pytest.approx(-0.05)
    assert soybean["price_proxy_count"] == 3


def test_focal_pattern_summary_marks_two_events_as_insufficient_evidence():
    matrix = pd.DataFrame({
        "event_id": ["1982-1983", "1997-1998"],
        "chain_id": ["cotton", "cotton"],
        "price_6m": [0.10, 0.20],
        "cta_medium_6m": [0.05, -0.02],
        "yield_deviation": [-0.10, -0.20],
    })

    summary = summarize_focal_patterns(matrix).set_index("chain_id")

    assert summary.loc["cotton", "valid_events"] == 2
    assert summary.loc["cotton", "evidence_strength"] == "INSUFFICIENT_EVIDENCE"
    assert summary.loc["cotton", "yield_negative_share"] == 1.0


def test_cta_tendency_is_not_mislabeled_as_full_climate_conversion():
    matrix = pd.DataFrame({
        "event_id": ["e1", "e2", "e3", "e4"],
        "chain_id": ["corn"] * 4,
        "weather_precip_anomaly": [-0.2, 0.1, -0.1, 0.2],
        "yield_deviation": [-0.1, 0.1, 0.1, -0.1],
        "price_6m": [-0.1, -0.2, -0.1, 0.1],
        "cta_medium_6m": [0.1, 0.2, 0.1, -0.1],
    })

    summary = summarize_focal_patterns(matrix).set_index("chain_id")

    assert summary.loc["corn", "cta_tendency"] == "CTA_TENDENCY"
    assert summary.loc["corn", "conversion_success_count"] == 0
    assert summary.loc["corn", "evidence_strength"] == "NO_STABLE_CONVERSION"
