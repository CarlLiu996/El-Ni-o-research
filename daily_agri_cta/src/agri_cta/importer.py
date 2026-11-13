"""Explicitly authorized private CSV/XLSX imports, never provider login scraping."""
import io
import pandas as pd
from .store import row

REQUIRED=['metric_id','commodity','observation_date','contract_code','price_type','frequency','value','unit','published_at']


def parse_authorized(body, extension, authorized=False):
    if not authorized:
        raise PermissionError('explicit local data-license authorization required')
    df=pd.read_excel(io.BytesIO(body)) if extension=='.xlsx' else pd.read_csv(io.BytesIO(body),keep_default_na=False)
    if set(REQUIRED)-set(df.columns):
        raise ValueError('missing import fields: '+str(sorted(set(REQUIRED)-set(df.columns))))
    result=[]
    keys={}
    for index,r in df.iterrows():
        try:
            date=pd.Timestamp(r['observation_date']).date().isoformat()
            published=None if pd.isna(r['published_at']) or not str(r['published_at']).strip() else pd.Timestamp(r['published_at'])
            if published is not None and published.tzinfo is None:
                raise ValueError('published_at needs timezone')
            item=row(str(r.metric_id),str(r.commodity),date,float(r.value),str(r.unit),str(r.frequency),
                     str(r.contract_code),str(r.price_type),published_at=published)
            key=tuple(item[k] for k in REQUIRED if k not in ['value','published_at'])
            if key in keys and keys[key]!=item['value']:
                raise ValueError('conflicting duplicate')
            keys[key]=item['value'];result.append(item)
        except Exception as e:
            raise ValueError(f'import row {index+2}: {e}') from e
    return result
