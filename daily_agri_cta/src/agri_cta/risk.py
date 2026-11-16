"""Official SDIC notice facts, with publication-day precision and explicit unknown scope.

Unqualified percentages never become company speculative/hedge rates. Discovery
and verified text facts are independent from a complete customer-rate baseline.
"""
import json
import re
from datetime import date
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from .store import digest

URL='https://www.sdicfutures.com.cn/annc/business/11098.html'
SYMBOLS=['SR','CF','RU','NR','M','C','P','Y','OI','RM']
CONTRACT=re.compile(r'(?<![A-Z])(?:SR|CF|RU|NR|M|C|P|Y|OI|RM)\d{3,4}(?!\d)')


def parse_article(body):
    soup=BeautifulSoup(body,'html.parser')
    article=soup.select_one('.news-detalis')
    if article is None:
        raise ValueError('official article container missing')
    heading=article.select_one('.font26')
    published=article.select_one('.time')
    content=article.select_one('.pAll8')
    title=heading.get_text(' ',strip=True) if heading else ''
    m=re.fullmatch(r'(\d{4})年(\d{1,2})月(\d{1,2})日交易提示',title)
    p=re.search(r'日期：\s*(\d{4}-\d{2}-\d{2})',published.get_text() if published else '')
    if not m or not p or content is None:
        raise ValueError('notice title/publication date/body schema changed')
    trade=date(*map(int,m.groups())).isoformat()
    pub=date.fromisoformat(p[1]).isoformat()
    if pub>trade:
        raise ValueError('publication date after applicable notice day')
    paragraphs=[x.get_text(' ',strip=True) for x in content.select('p') if x.get_text(strip=True)]
    if not paragraphs:
        raise ValueError('empty/header-only official article')
    facts=[];unmapped=[]
    for paragraph in paragraphs:
        for sentence in re.split(r'[。；;]',paragraph):
            context=[]
            for sentence in re.split('，',sentence):
                upper=sentence.upper()
                mentioned=re.findall(r'(?<![A-Z])[A-Z]{1,2}\d{3,4}(?!\d)',upper)
                if mentioned:
                    context=CONTRACT.findall(upper)
                contracts=context
                kind=None;value=None;detail=None;scope='unknown';side='unknown'
                # Restriction/preference checks precede numeric margin checks deliberately.
                if '自然人' in sentence and any(x in sentence for x in ['清仓','归零','不得持仓','持仓为0','持仓调整为0']):
                    kind,detail='delivery_restriction','natural_person_position_zero'
                elif '保证金' in sentence and re.search(r'(退出|取消|不再享受|终止).{0,6}优惠|优惠.{0,6}(退出|取消|终止)',sentence):
                    kind,detail='margin_preference','preference_withdrawal'
                elif '最后交易日' in sentence:
                    kind,detail='delivery_restriction','last_trading_day'
                elif re.search(r'保证金率(?:调整为|为)\s*\d',sentence):
                    kind='margin_rate_unspecified_scope'
                    value=float(re.search(r'保证金率(?:调整为|为)\s*(\d+(?:\.\d+)?)%',sentence)[1])
                    if '公司客户投机' in sentence:
                        kind,scope,side='company_speculative_margin','company','speculative'
                    elif '公司客户套保' in sentence:
                        kind,scope,side='company_hedge_margin','company','hedge'
                    elif '交易所投机' in sentence:
                        kind,scope,side='exchange_speculative_margin','exchange','speculative'
                    elif '交易所套保' in sentence:
                        kind,scope,side='exchange_hedge_margin','exchange','hedge'
                elif '合约上市' in sentence:
                    kind,detail='contract_listing','listing'
                if kind and not contracts:
                    unmapped.append('relevant rule text lacks explicit covered-product contracts')
                for contract in sorted(set(contracts)) if kind else []:
                    facts.append(dict(contract_code=contract,rule_type=kind,restriction_kind=detail,
                        scope=scope,position_side=side,previous_value=None,new_value=value,
                        previous_value_status='unknown',delta=None,unit='percent' if value is not None else None,
                        verification_status='verified_text_scope_unknown' if kind=='margin_rate_unspecified_scope' else 'verified_text'))
                # Limits may share a sentence with a margin change, but have their own field.
                limit=re.search(r'涨跌停板幅度(?:调整)?为[±+－-]?(\d+(?:\.\d+)?)%',sentence)
                if limit:
                    for contract in sorted(set(contracts)):
                        facts.append(dict(contract_code=contract,rule_type='price_limit',restriction_kind=None,
                            scope='unknown',position_side='unknown',previous_value=None,new_value=float(limit[1]),
                            previous_value_status='unknown',delta=None,unit='percent',verification_status='verified_text'))
    return dict(title=title,published_date=pub,published_at=None,publication_precision='date',
                applicable_trade_date=trade,facts=facts,unmapped_rule_warnings=unmapped)


def discover(store, limit=12):
    db=store.db
    db.execute('''CREATE TABLE IF NOT EXISTS notice_seen (
      source_url VARCHAR, document_id VARCHAR, first_seen TIMESTAMPTZ,
      title VARCHAR, status VARCHAR, PRIMARY KEY(source_url,document_id));
      CREATE TABLE IF NOT EXISTS risk_fact (
        fact_id VARCHAR PRIMARY KEY,document_id VARCHAR,source_url VARCHAR,first_seen TIMESTAMPTZ,
        published_date DATE,applicable_trade_date DATE,fact_json VARCHAR);
    ''')
    doc,body=store.fetch('SDIC_NOTICE',URL)
    soup=BeautifulSoup(body,'html.parser')
    links=[]
    for a in soup.select('a[href]'):
        title=a.get_text(' ',strip=True)
        url=urljoin(URL,a['href'])
        if re.fullmatch(r'·?\s*\d{4}年\d+月\d+日交易提示',title):
            if urlsplit(url).hostname!='www.sdicfutures.com.cn':
                raise ValueError('notice link left official host')
            links.append((title,url))
    if not links:
        store.reject(doc,'no dated official notice links')
        raise ValueError('notice list unavailable/header-only')
    db.execute("ALTER TABLE risk_fact ADD COLUMN IF NOT EXISTS parser_version VARCHAR DEFAULT 'legacy'")
    db.execute("ALTER TABLE risk_fact ADD COLUMN IF NOT EXISTS validation_status VARCHAR DEFAULT 'unverified_legacy_parser'")
    db.execute("CREATE OR REPLACE VIEW accepted_risk_text AS SELECT * FROM risk_fact WHERE validation_status='accepted_text_fact'")
    output=[]
    for title,url in links[:limit]:
        article=None
        try:
            article,raw=store.fetch('SDIC_ARTICLE',url)
            parsed=parse_article(raw)
            first=not bool(db.execute('SELECT 1 FROM notice_seen WHERE source_url=? AND document_id=?',[url,article]).fetchone())
            db.execute("INSERT OR IGNORE INTO notice_seen VALUES (?,?,current_timestamp,?,'parsed_text_baseline_incomplete')",[url,article,title])
            newfacts=0
            for fact in parsed['facts']:
                encoded=json.dumps(fact,sort_keys=True,ensure_ascii=False)
                fid=digest(('sdic-clauses-v2'+url+parsed['published_date']+encoded).encode())
                if not db.execute('SELECT 1 FROM risk_fact WHERE fact_id=?',[fid]).fetchone():
                    newfacts+=1
                    db.execute("INSERT INTO risk_fact VALUES (?,?,?,current_timestamp,?,?,?,'sdic-clauses-v2','accepted_text_fact')",
                        [fid,article,url,parsed['published_date'],parsed['applicable_trade_date'],encoded])
            db.execute("UPDATE source_document SET parse_status='parsed',error_message=NULL WHERE document_id=?",[article])
            output.append(dict(url=url,document_id=article,new_content=first,new_fact_count=newfacts,
                               status='parsed_text_baseline_incomplete',parser_version='sdic-clauses-v2',**parsed))
        except Exception as e:
            if article:
                store.reject(article,e)
            output.append(dict(title=title,url=url,status='unverified',error=str(e)))
    return output
