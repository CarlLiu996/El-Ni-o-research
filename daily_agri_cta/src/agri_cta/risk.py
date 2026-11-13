"""Official notice discovery and content change ledger; rates remain unverified.

No guessed rate extraction: an article/attachment needs a verified mapping before
creating a company_margin_notice. A page-only success never means 'no changes'.
"""
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup

URL='https://www.sdicfutures.com.cn/annc/business/11098.html'


def discover(store, limit=3):
    db=store.db
    db.execute('''CREATE TABLE IF NOT EXISTS notice_seen (
      source_url VARCHAR, document_id VARCHAR, first_seen TIMESTAMPTZ,
      title VARCHAR, status VARCHAR, PRIMARY KEY(source_url,document_id))''')
    doc,body=store.fetch('SDIC_NOTICE',URL)
    soup=BeautifulSoup(body,'html.parser')
    links=[]
    for a in soup.select('a[href]'):
        title=a.get_text(' ',strip=True)
        if re.search(r'\d{4}年\d+月\d+日交易提示',title):
            links.append((title,urljoin(URL,a['href'])))
    if not links:
        store.reject(doc,'no dated official notice links')
        raise ValueError('notice list unavailable/header-only')
    output=[]
    for title,url in links[:limit]:
        try:
            article,raw=store.fetch('SDIC_ARTICLE',url)
            seen=db.execute('SELECT 1 FROM notice_seen WHERE source_url=? AND document_id=?',[url,article]).fetchone()
            db.execute("INSERT OR IGNORE INTO notice_seen VALUES (?,?,current_timestamp,?,'unverified_rate_mapping')",[url,article,title])
            output.append(dict(title=title,url=url,document_id=article,new_content=not bool(seen),
                status='unverified_rate_mapping',previous_value=None,new_value=None,
                warning='notice date in title is applicable trading date, not publication timestamp'))
        except Exception as e:
            output.append(dict(title=title,url=url,status='unverified',error=str(e)))
    return output
