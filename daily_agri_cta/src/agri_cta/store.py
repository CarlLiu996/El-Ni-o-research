"""Append-only value vintages; every network attempt has an independent snapshot."""
import hashlib
import json
import math
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import requests


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value).hexdigest()


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = duckdb.connect(str(self.root / 'history.duckdb'))
        self.db.execute('''CREATE TABLE IF NOT EXISTS source_document (
          document_id VARCHAR PRIMARY KEY, source_id VARCHAR, source_url VARCHAR,
          fetched_at TIMESTAMPTZ, sha256 VARCHAR, raw_path VARCHAR,
          http_status INTEGER, content_type VARCHAR, rights_class VARCHAR,
          parse_status VARCHAR, error_message VARCHAR);
        CREATE TABLE IF NOT EXISTS fetch_attempt (
          attempt_id VARCHAR, document_id VARCHAR, source_id VARCHAR, source_url VARCHAR,
          fetched_at TIMESTAMPTZ, raw_path VARCHAR, http_status INTEGER, error_message VARCHAR);
        CREATE TABLE IF NOT EXISTS observation (
          observation_id VARCHAR PRIMARY KEY, series_key VARCHAR, metric_id VARCHAR,
          commodity VARCHAR, observation_date DATE, contract_code VARCHAR,
          price_type VARCHAR, frequency VARCHAR, value DOUBLE, unit VARCHAR,
          document_id VARCHAR, published_at TIMESTAMPTZ, collected_at TIMESTAMPTZ,
          revision_status VARCHAR, data_status VARCHAR, vintage INTEGER, source_flag VARCHAR);
        CREATE TABLE IF NOT EXISTS reported_evidence (
          report_date DATE, observation_id VARCHAR, evidence_tag VARCHAR,
          PRIMARY KEY(report_date, observation_id));
        CREATE OR REPLACE VIEW history AS SELECT o.*, d.source_id,d.source_url,d.sha256,
          d.fetched_at FROM observation o JOIN source_document d USING(document_id);
        CREATE OR REPLACE VIEW latest AS SELECT * FROM history WHERE data_status IN ('verified','provisional')
          QUALIFY row_number() OVER(PARTITION BY series_key ORDER BY vintage DESC)=1;''')

        # v0 scratch runs used exchange field names as price_type. Retain duplicate
        # rows for audit, but exclude them from the canonical verified view.
        for oid,key,value in self.db.execute("SELECT observation_id,series_key,value FROM observation WHERE price_type IN ('openprice','highestprice','lowestprice') AND data_status IN ('verified','provisional')").fetchall():
            fields=json.loads(key)
            fields[4]={'openprice':'open','highestprice':'high','lowestprice':'low'}[fields[4]]
            normalized=json.dumps(fields,ensure_ascii=False)
            canonical=self.db.execute('SELECT value FROM observation WHERE series_key=? ORDER BY vintage DESC LIMIT 1',[normalized]).fetchone()
            if canonical:
                status='duplicate_schema_migration' if canonical[0]==value else 'unverified_schema_conflict'
                self.db.execute('UPDATE observation SET data_status=? WHERE observation_id=?',[status,oid])
            else:
                self.db.execute('UPDATE observation SET series_key=?,price_type=? WHERE observation_id=?',[normalized,fields[4],oid])

        # Store portable paths relative to the data root (also migrate scratch snapshots).
        for table,ident in [('source_document','document_id'),('fetch_attempt','attempt_id')]:
            for key,path in self.db.execute(f'SELECT {ident},raw_path FROM {table}').fetchall():
                parts=Path(path).parts
                if 'raw' in parts:
                    relative=str(Path(*parts[parts.index('raw'):]))
                    if relative!=path:
                        self.db.execute(f'UPDATE {table} SET raw_path=? WHERE {ident}=?',[relative,key])

    def fetch(self, source, url):
        t, attempt = now(), uuid.uuid4().hex
        directory = self.root / 'raw' / source / attempt
        directory.mkdir(parents=True)
        status, body, ctype, error = None, b'', None, None
        try:
            r = requests.get(url, timeout=40)
            status, body, ctype = r.status_code, r.content, r.headers.get('Content-Type')
            r.raise_for_status()
        except Exception as e:
            error = str(e)
        path = directory / 'response.bin'
        path.write_bytes(body)
        sha = digest(body)
        doc = digest((url + sha).encode())
        metadata = dict(source_url=url, fetched_at=t, sha256=sha, http_status=status,
                        error=error, document_id=doc, content_type=ctype)
        (directory / 'metadata.json').write_text(json.dumps(metadata, indent=2))
        self.db.execute('INSERT INTO fetch_attempt VALUES (?,?,?,?,?,?,?,?)',
                        [attempt, doc, source, url, t, str(path.relative_to(self.root)), status, error])
        self.db.execute('INSERT OR IGNORE INTO source_document VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                        [doc,source,url,t,sha,str(path.relative_to(self.root)),status,ctype,'restricted_pending_license',
                         'unverified' if error else 'fetched',error])
        if error:
            raise RuntimeError(error)
        return doc, body

    def reject(self, doc, error):
        self.db.execute("UPDATE source_document SET parse_status='unverified',error_message=? WHERE document_id=?", [str(error),doc])

    def ingest(self, doc, rows):
        if not rows:
            raise ValueError('empty/header-only source')
        if not self.db.execute("SELECT 1 FROM source_document WHERE document_id=?",[doc]).fetchone():
            raise ValueError("unknown source document")
        keys = {}
        # Validate the complete document before writing any observation.
        for r in rows:
            date.fromisoformat(r['observation_date'])
            key = tuple(r[k] for k in ['metric_id','commodity','observation_date','contract_code','price_type','frequency','unit'])
            if key in keys and keys[key] != r['value']:
                raise ValueError('conflicting duplicate in source document')
            keys[key] = r['value']
            if not math.isfinite(r['value']):
                raise ValueError('nonfinite value')
            if r['observation_date'] > datetime.now(timezone.utc).date().isoformat():
                raise ValueError('future observation')
            if r['metric_id'] != 'nino34_anomaly' and r['value'] < 0:
                raise ValueError('negative market quantity/price')
            if not r['unit'] or not r['frequency']:
                raise ValueError('unknown unit/frequency')
        added = 0
        self.db.execute("BEGIN TRANSACTION")
        try:
            for r in rows:
                key = json.dumps([r[k] for k in ['metric_id','commodity','observation_date','contract_code','price_type','frequency','unit']],ensure_ascii=False)
                old = self.db.execute('SELECT value,vintage,source_flag FROM observation WHERE series_key=? ORDER BY vintage DESC LIMIT 1',[key]).fetchone()
                if old and old[0] == r['value'] and old[2] == r.get('source_flag',''):
                    continue
                vintage = old[1]+1 if old else 1
                oid = digest((key+str(vintage)).encode())
                self.db.execute('INSERT INTO observation VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  [oid,key,r['metric_id'],r['commodity'],r['observation_date'],r['contract_code'],
                   r['price_type'],r['frequency'],r['value'],r['unit'],doc,r.get('published_at'),now(),
                   'revision_detected' if old else 'first_seen',r.get('data_status','verified'),vintage,r.get('source_flag','')])
                added += 1
            self.db.execute("UPDATE source_document SET parse_status='parsed',error_message=NULL WHERE document_id=?",[doc])
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return added

    def export(self):
        for table in ['source_document','fetch_attempt','observation','reported_evidence']:
            path = str((self.root / (table+'.parquet')).resolve()).replace("'", "''")
            self.db.execute(f"COPY {table} TO '{path}' (FORMAT PARQUET)")


def row(metric, commodity, date, value, unit, frequency='monthly', contract='', price_type='non_price', **extra):
    return dict(metric_id=metric,commodity=commodity,observation_date=date,value=float(value),unit=unit,
                frequency=frequency,contract_code=contract,price_type=price_type,**extra)
