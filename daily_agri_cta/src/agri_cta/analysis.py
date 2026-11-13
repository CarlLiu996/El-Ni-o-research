from datetime import datetime


def evidence_tag(published, first_seen, previous, freeze, already_reported=False):
    if already_reported:
        return 'CONTINUING'
    # Actual ingest time bounds visibility even when publication claims an earlier time.
    if first_seen > freeze or (published and published > freeze):
        return 'NOT_YET_VISIBLE'
    if published and previous < published <= freeze:
        return 'TODAY_NEW'
    if published and published.isocalendar()[:2] == freeze.isocalendar()[:2]:
        return 'WEEK_NEW'
    return 'FIRST_SEEN_UNKNOWN_RELEASE' if published is None and previous < first_seen <= freeze else 'BACKGROUND'


def matched_difference(a,b):
    for key in ['observation_date','price_type','unit']:
        if a[key]!=b[key]:
            raise ValueError(f'incompatible {key}')
    if not a.get('contract_code') or not b.get('contract_code'):
        raise ValueError('explicit contracts required')
    return a['value']-b['value']


def ecdf(values, minimum=30):
    if len(values)<minimum:
        return None
    return sum(v<=values[-1] for v in values)/len(values)


def risk_change(rule_type,new_value,previous_value=None):
    allowed={'company_speculative_margin','company_hedge_margin','exchange_speculative_margin',
             'exchange_hedge_margin','price_limit','margin_preference','delivery_restriction'}
    if rule_type not in allowed:
        raise ValueError('unknown rule type')
    return dict(rule_type=rule_type,new_value=new_value,previous_value=previous_value,
                previous_value_status='unknown' if previous_value is None else 'verified',
                delta=None if previous_value is None or rule_type=='delivery_restriction' else new_value-previous_value)
