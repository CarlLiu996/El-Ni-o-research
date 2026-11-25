"""Synthetic HTML: no live notices or licensed source text in fixtures."""
import pytest
from agri_cta.risk import parse_article


def html(text):
    return ('<div class="news-detalis"><div class="font26">2026年11月12日交易提示</div>'
            '<div class="time">日期：2026-11-11</div><div class="pAll8"><p>'+text+'</p></div></div>').encode()


def test_margin_scope_unknown_never_company_default():
    r=parse_article(html('nr2611、ru2611合约保证金率调整为22%，下一日的涨跌停板幅度为±9%。'))
    assert len(r['facts'])==4
    assert r['published_at'] is None and r['published_date']=='2026-11-11'
    for f in r['facts']:
        assert f['previous_value'] is None and f['delta'] is None
    assert {f['rule_type'] for f in r['facts']}=={'margin_rate_unspecified_scope','price_limit'}


def test_restrictions_and_preferences_are_not_margin_hikes():
    r=parse_article(html('RU2611、NR2611自然人持仓归零。RU2611保证金取消优惠，不再享受优惠。'))
    assert {f['rule_type'] for f in r['facts']}=={'delivery_restriction','margin_preference'}
    assert all(f['new_value'] is None for f in r['facts'])


def test_company_and_exchange_and_hedge_are_separate():
    r=parse_article(html('RU2611公司客户投机保证金率调整为22%。RU2611交易所套保保证金率调整为12%。'))
    assert {f['rule_type'] for f in r['facts']}=={'company_speculative_margin','exchange_hedge_margin'}


def test_last_trading_day_not_natural_person_zero_or_rate_change():
    r=parse_article(html('RU2611、NR2611合约最后交易日。'))
    assert all(f['restriction_kind']=='last_trading_day' for f in r['facts'])


def test_header_only_fail_closed_and_other_product_not_matched():
    with pytest.raises(ValueError):parse_article(b'<div>headers only</div>')
    assert not parse_article(html('SM2611合约保证金率调整为22%。'))['facts']


def test_separate_rates_inside_same_sentence_and_limit_context():
    r=parse_article(html('BU2611合约保证金率调整为33%，AU2611合约保证金率调整为29%，HC2611、RU2611合约保证金率调整为22%。NR2611出现单边市，保证金率调整为19%，下一日的涨跌停板幅度为±9%。'))
    facts={(f['contract_code'],f['rule_type']):f['new_value'] for f in r['facts']}
    assert facts=={('RU2611','margin_rate_unspecified_scope'):22,('NR2611','margin_rate_unspecified_scope'):19,('NR2611','price_limit'):9}
