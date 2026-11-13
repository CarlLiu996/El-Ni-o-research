"""Audit reachability separately from successful structured parsing."""
import csv
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser


def audit(store, output):
    registry = Path(__file__).resolve().parents[2]/'config/source_registry.csv'
    # Installed editable and checkout execution both use project-relative registry.
    if not registry.exists():
        registry=Path('config/source_registry.csv')
    rows=[]
    for r in csv.DictReader(registry.open()):
        url=r['official_url']
        if not url.startswith('https://'):
            rows.append(dict(source=r['source_id'],url=url,status='requires_authorization',bytes=0))
            continue
        robots_url=f'https://{urlsplit(url).netloc}/robots.txt'
        denied=False
        for kind,u in [('robots',robots_url),('page',url)]:
            if kind=='page' and denied:
                rows.append(dict(source=r['source_id'],kind=kind,url=u,status='not_requested_robots_disallow'))
                continue
            try:
                doc,body=store.fetch(r['source_id']+'_AUDIT',u)
                if kind=='robots':
                    robot=RobotFileParser();robot.parse(body.decode('utf-8',errors='replace').splitlines())
                    denied=not robot.can_fetch('*',url)
                rows.append(dict(source=r['source_id'],kind=kind,url=u,status='page_only_http_'+str(store.db.execute('SELECT http_status FROM source_document WHERE document_id=?',[doc]).fetchone()[0]),bytes=len(body),document_id=doc))
            except Exception as e:
                rows.append(dict(source=r['source_id'],kind=kind,url=u,status='unverified',error=str(e)))
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    import json
    (out/'connectivity.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    store.export()
