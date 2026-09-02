from __future__ import annotations

import pandas as pd
import pytest

from elnino_cta.sources import china_sugar as sugar


YUNNAN = '''<html><title>云南：25/26榨季截止7月末销糖率</title><body>
<h1>云南：25/26榨季截止7月末销糖率</h1>2026-08-04 16:25:26
云南省2025/2026榨季，产糖293.83万吨（上榨季同期产糖241.88万吨）。
截至2026年7月31日，累计销售食糖203.17万吨（去年同期销糖195.14万吨），
销糖率69.15%（去年同期销糖率80.68%）。
工业库存90.65万吨（去年同期工业库存46.73万吨）。</body></html>'''
GUANGXI = '''<html><title>广西：25/26榨季截至7月底产销</title><body>
<h1>广西：25/26榨季截至7月底产销</h1>2026-08-06 09:32:54
广西2025/26年榨季，产混合糖769.74万吨，同比增加123.24万吨。
截至7月31日，累计销糖548.40万吨，同比减少1.21万吨；产销率71.24%，同比降低13.77个百分点。
工业库存221.34万吨，同比增加124.45万吨。</body></html>'''
CASDE = '''<html><title>2026年8月中国农产品供需形势分析</title><body>日期：2026-08-12
<table><tr><td>指标</td><td>2024/25</td><td>2025/26（8月估计）</td><td>2026/27（8月预测）</td></tr>
<tr><td>万吨（10000 tons）</td><td></td><td></td><td></td></tr>
<tr><td>食糖产量</td><td>1116</td><td>1295</td><td>1293</td></tr>
<tr><td>甘蔗糖</td><td>960</td><td>1138</td><td>1143</td></tr>
<tr><td>甜菜糖</td><td>156</td><td>157</td><td>150</td></tr>
<tr><td>进口</td><td>462</td><td>500</td><td>480</td></tr>
<tr><td>消费</td><td>1567</td><td>1570</td><td>1573</td></tr>
<tr><td>出口</td><td>16</td><td>18</td><td>18</td></tr>
<tr><td>结余变化</td><td>-5</td><td>207</td><td>182</td></tr></table></body></html>'''


def test_province_uses_reported_stock_not_production_minus_sales():
    df = sugar.parse_province_report(YUNNAN, region="云南", observation_date="2026-07-31", source_url="https://example.com/yunnan")
    rows = df.set_index("metric")
    assert rows.loc["inventory", "value"] == 90.65  # 293.83-203.17=90.66, do not overwrite the source.
    assert rows.loc["inventory", "prior_value"] == 46.73
    assert rows.loc["sales_rate", "change"] == pytest.approx(-11.53)
    assert set(df.published_at) == {"2026-08-04"}
    assert set(df.crop_year) == {"2025/26"}
    assert set(df.value_type) == {"actual"}


def test_province_derives_same_period_baseline_from_reported_change():
    rows = sugar.parse_province_report(GUANGXI, region="广西", observation_date="2026-07-31", source_url="https://example.com/gx").set_index("metric")
    assert rows.loc["production", "prior_value"] == pytest.approx(646.5)
    assert rows.loc["sales", "prior_value"] == pytest.approx(549.61)
    assert rows.loc["sales_rate", "prior_value"] == pytest.approx(85.01)


def test_single_month_production_does_not_replace_crop_year_total():
    html = GUANGXI.replace("</body>", "其中7月份广西单月产糖0.23万吨，同比增加0.23万吨。</body>")
    rows = sugar.parse_province_report(html, region="广西", observation_date="2026-07-31", source_url="x").set_index("metric")
    assert rows.loc["production", "value"] == 769.74


def test_province_rejects_wrong_month_and_forecast():
    with pytest.raises(ValueError):
        sugar.parse_province_report(YUNNAN, region="云南", observation_date="2026-06-30", source_url="x")
    with pytest.raises(ValueError):
        sugar.parse_province_report(YUNNAN.replace("截止7月末销糖率", "预计7月销量"), region="云南", observation_date="2026-07-31", source_url="x")


def test_casde_preserves_vintages_forecasts_and_balance_not_stock():
    df = sugar.parse_casde(CASDE, source_url="https://example.com/casde")
    production = df.query("metric == 'production'").set_index("crop_year")
    assert production.loc["2025/26", "value"] == 1295
    assert production.loc["2025/26", "value_type"] == "estimate"
    assert production.loc["2026/27", "value_type"] == "forecast"
    assert set(df.published_at) == {"2026-08-12"}
    assert "balance_change" in set(df.metric)
    assert "inventory" not in set(df.metric)


def test_snapshot_excludes_future_publications_and_stale_report_is_not_current():
    df = sugar.parse_province_report(YUNNAN, region="云南", observation_date="2026-07-31", source_url="x")
    assert sugar.current_province_rows(df, "2026-08-03").empty
    current = sugar.current_province_rows(df, "2026-08-31").set_index("metric")
    assert current.loc["inventory", "direction"] == "库存高于去年同期"
    assert "末端消费" in current.loc["sales", "limitation"]
    stale = sugar.current_province_rows(df, "2026-11-01")
    assert set(stale.status) == {"STALE"}
    assert all("过期" in item for item in stale.direction)


def test_price_parser_maps_named_fields_and_never_uses_positional_order():
    raw = {"category": ["2026-08-27", "2026-08-28"], "data": {"现货价格": [5000, 5010], "原糖价格": [17, 18], "综合价格": [5100, 5110]}}
    result = sugar.parse_price_json(raw)
    assert result.iloc[-1].spot_price == 5010
    assert result.iloc[-1].raw_sugar_price == 18
    assert result.iloc[-1].composite_price == 5110


def test_price_parser_rejects_misaligned_series():
    with pytest.raises(ValueError):
        sugar.parse_price_json({"category": ["2026-08-28"], "data": {"现货价格": [1, 2], "原糖价格": [17], "综合价格": [2]}})


def test_import_report_does_not_mix_monthly_and_cumulative_volume():
    html = '<title>2026年7月份我国进口食糖47万吨</title><h1>食糖进口</h1>2026-08-18 10:33:00<p>2026年7月份我国进口食糖47万吨，同比减少27万吨。2026年1-7月我国累计进口食糖160万吨。</p>'
    df = sugar.parse_import_report(html, observation_date="2026-07-31", source_url="https://example.com/imports")
    assert df.iloc[0].value == 47
    assert df.iloc[0].published_at == "2026-08-18"
    assert df.iloc[0].status == "REVIEW_REQUIRED"


def test_refresh_pipeline_uses_cached_sources_and_records_failures(tmp_path):
    import json
    from scripts.fetch_china_sugar import collect

    raw = tmp_path / "data/raw/china_sugar"
    raw.mkdir(parents=True)
    (raw / "yn.html").write_text(YUNNAN, encoding="utf-8")
    (raw / "balance.html").write_text(CASDE, encoding="utf-8")
    manifest = collect(tmp_path, [
        {"id": "yn", "kind": "province", "region": "云南", "date": "2026-07-31", "url": "https://example.com/yn"},
        {"id": "balance", "kind": "casde", "url": "https://example.com/casde"},
        {"id": "absent", "kind": "province", "region": "广西", "date": "2026-07-31", "url": "https://example.com/gx"},
    ], as_of="2026-08-31", offline=True)
    assert manifest["sources"]["absent"]["status"] == "FETCH_ERROR"
    assert manifest["sources"]["yn"]["rows"] == 4
    output = pd.read_csv(tmp_path / "data/processed/china_sugar/province_history.csv")
    assert len(output) == 4
    assert output.query("metric == 'inventory'").iloc[0].value == 90.65
    assert json.loads((tmp_path / "data/processed/china_sugar/manifest.json").read_text(encoding="utf-8"))["as_of"] == "2026-08-31"
