"""Parsers for official published tables; structure changes raise, never fill gaps."""
import calendar
import io
import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .store import row

NOAA = 'https://www.cpc.ncep.noaa.gov/data/indices/ersst5.nino.mth.91-20.ascii'
MPOB = {
  2023: 'https://bepi.mpob.gov.my/stat/web_internal.php?val=202384',
  2024: 'https://bepi.mpob.gov.my/stat/web_internal.php?val=202484',
  2025: 'https://bepi.mpob.gov.my/stat/web_internal.php?val=202584',
  2026: 'https://bepi.mpob.gov.my/stat/web_report1.php?val=202684',
}
SHFE = 'https://www.shfe.cn'


def month_end(year, month):
    return f'{year:04}-{month:02}-{calendar.monthrange(year,month)[1]:02}'


def noaa(body):
    text = body.decode('ascii')
    if 'NINO3.4  ANOM' not in text.splitlines()[0]:
        raise ValueError('NOAA schema changed')
    result = []
    for line in text.splitlines()[1:]:
        f = line.split()
        if len(f) != 10:
            raise ValueError('NOAA row width')
        y,m = map(int, f[:2])
        value = float(f[9])
        if abs(value) > 20:
            raise ValueError('NOAA invalid/sentinel anomaly')
        result.append(row('nino34_anomaly','ENSO',month_end(y,m),value,'degC',
                          source_flag='ERSSTv5;1991-2020'))
    return result


def mpob(body, year):
    soup = BeautifulSoup(body,'html.parser')
    if f'SUMMARY OF THE MALAYSIAN PALM OIL INDUSTRY  {year}' not in soup.get_text() and not re.search(r'INDUSTRY\s+'+str(year),soup.get_text()):
        raise ValueError('MPOB title/year mismatch')
    rows = [[c.get_text(' ',strip=True) for c in tr.select('td')] for tr in soup.select('tr')]
    header = next((r for r in rows if len(r)==14 and r[1].startswith('Dec') and r[2].startswith('Jan')),None)
    if not header:
        raise ValueError('MPOB 13-month schema changed')
    if str(year)[-2:] not in header[2] or str(year-1)[-2:] not in header[1]:
        raise ValueError('MPOB year header mismatch')
    targets = {('PRODUCTION (TONNES)','Crude Palm Oil'):'mpob_production',
               ('CLOSING STOCK (TONNES)','Palm Oil'):'mpob_stock',
               ('EXPORT (TONNES)','Palm Oil'):'mpob_exports'}
    section, result, seen = '', [], set()
    for r in rows:
        if len(r)==1:
            section = r[0]
        metric = targets.get((section,r[0])) if r else None
        if not metric:
            continue
        if len(r)!=14 or metric in seen:
            raise ValueError('MPOB duplicate/short target row')
        seen.add(metric)
        # Exclude previous December: the prior annual table owns that month.
        for m in range(1,13):
            val = r[m+1].replace(',','').strip()
            if not val:
                continue
            if not re.fullmatch(r'\d+(\.\d+)?',val):
                raise ValueError('MPOB nonnumeric cell')
            flag = 'preliminary' if '(p)' in header[m+1] else 'revised' if '(r)' in header[m+1] else ''
            result.append(row(metric,'P',month_end(year,m),float(val),'tonnes',
                              data_status='provisional' if flag=='preliminary' else 'verified',source_flag=flag))
    if seen != set(targets.values()):
        raise ValueError('MPOB missing target section')
    return result


def shfe_day(body, date):
    j = json.loads(body)
    # Exchange day is authoritative; never split a night session by wall-clock day.
    actual = str(j.get('report_date') or j.get('o_trade_day')).replace('-','')
    if actual != date.replace('-',''):
        raise ValueError(f'SHFE trading date mismatch: {actual} != {date}')
    result = []
    fields = {'OPENPRICE':('futures_open','CNY/tonne'),'HIGHESTPRICE':('futures_high','CNY/tonne'),
              'LOWESTPRICE':('futures_low','CNY/tonne'),'CLOSEPRICE':('futures_close','CNY/tonne'),
              'SETTLEMENTPRICE':('futures_settlement','CNY/tonne'),'VOLUME':('futures_volume','contracts'),
              'OPENINTEREST':('futures_open_interest','contracts')}
    for r in j['o_curinstrument']:
        product = str(r.get('PRODUCTGROUPID','')).strip().lower()
        if product not in ['ru','nr'] or not re.fullmatch(r'\d{4}',str(r['DELIVERYMONTH']).strip()):
            continue
        contract = product.upper()+str(r['DELIVERYMONTH']).strip()
        for f,(metric,unit) in fields.items():
            v = r[f]
            if v in ['',None]:
                continue  # Untraded contract: missing price stays missing, never forward-filled.
            if 'PRICE' in f and float(v)<=0:
                raise ValueError('invalid contract price')
            result.append(row(metric,product.upper(),date,v,unit,'daily',contract,
                              {'OPENPRICE':'open','HIGHESTPRICE':'high','LOWESTPRICE':'low','CLOSEPRICE':'close','SETTLEMENTPRICE':'settlement','VOLUME':'volume','OPENINTEREST':'openinterest'}[f]))
    return result


def shfe_receipts(body, date):
    soup = BeautifulSoup(body,'html.parser')
    text = soup.get_text(' ',strip=True)
    y,m,d = map(int,date.split('-'))
    if not re.search(fr'{y}\D+0?{m}\D+0?{d}\D',text):
        raise ValueError('warehouse date missing/mismatch')
    result = []
    for table in soup.select('table'):
        t = table.get_text(' ',strip=True)
        symbol = 'RU' if '天然橡胶' in t else 'NR' if '20号胶' in t else None
        if symbol is None:
            continue
        if '单位：吨' not in t and '单位:吨' not in t:
            raise ValueError('warehouse unit unknown')
        totals = []
        for tr in table.select('tr'):
            cells = [c.get_text(' ',strip=True) for c in tr.select('td')]
            if cells and cells[0]=='总计':
                totals.append(cells)
        if not totals and symbol=='NR' and '厂库' in t:
            candidates = [[c.get_text(' ',strip=True) for c in tr.select('td')] for tr in table.select('tr')]
            candidates = [c for c in candidates if len(c)>=3 and re.fullmatch(r'[0-9,]+',c[-2]) and re.fullmatch(r'-?[0-9,]+',c[-1])]
            if len(candidates)==1:
                totals = candidates
        if len(totals)!=1:
            raise ValueError('warehouse total missing/ambiguous')
        subtype = '_factory' if '厂库' in t.split('单位')[0] else '_warehouse'
        result.append(row('warehouse_receipts'+subtype,symbol,date,
                          float(totals[0][-2].replace(',','')),'tonnes','daily'))
    return result


def collect_one(store, source, url, parser, *args):
    doc = None
    try:
        doc, body = store.fetch(source,url)
        rows = parser(body,*args)
        n = store.ingest(doc,rows)
        return dict(source=source,url=url,status='parsed',rows=len(rows),added=n)
    except Exception as e:
        if doc:
            store.reject(doc,e)
        return dict(source=source,url=url,status='unverified',error=str(e))


def shfe_archive(body, year):
    import zipfile
    import openpyxl
    result=[]
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        members=[n for n in archive.namelist() if n.endswith('.xlsx')]
        if not members:
            raise ValueError('empty official archive')
        for name in members:
            book=openpyxl.load_workbook(io.BytesIO(archive.read(name)),read_only=True,data_only=True)
            for sheet in book:
                rows=sheet.iter_rows(values_only=True)
                next(rows);next(rows)
                cn=next(rows);en=next(rows)
                if tuple(en[:2])!=('Contract','Date') or en[7]!='Close' or en[8]!='Settle' or en[11]!='Volume' or en[13]!='OI':
                    raise ValueError('archive column schema changed')
                for cells in rows:
                    contract=str(cells[0]).upper()
                    if not re.fullmatch(r'(RU|NR)\d{4}',contract):
                        continue
                    d=str(cells[1]);date=f'{d[:4]}-{d[4:6]}-{d[6:8]}'
                    if len(d)!=8 or not d.startswith(str(year)):
                        raise ValueError('archive year/date mismatch')
                    for i,metric,unit in [(4,'open','CNY/tonne'),(5,'high','CNY/tonne'),(6,'low','CNY/tonne'),
                       (7,'close','CNY/tonne'),(8,'settlement','CNY/tonne'),(11,'volume','contracts'),(13,'open_interest','contracts')]:
                        value=cells[i]
                        if value is None or value=='':
                            continue
                        if unit=='CNY/tonne' and float(value)<=0:
                            raise ValueError('invalid archive price')
                        result.append(row('futures_'+metric,contract[:2],date,value,unit,'daily',contract,
                                          metric if i<9 else ('volume' if i==11 else 'openinterest')))
            book.close()
    return result
