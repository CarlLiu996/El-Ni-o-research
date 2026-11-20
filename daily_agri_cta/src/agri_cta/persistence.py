"""Private S3 snapshots with checksum verification and staged, atomic restoration."""
import io
import json
import shutil
import tarfile
import tempfile
import uuid
from datetime import datetime,timezone
from pathlib import Path,PurePosixPath


def sync(data,bucket,prefix,restore=False):
    import boto3
    from .store import digest
    client=boto3.client('s3')
    guard=client.get_public_access_block(Bucket=bucket)['PublicAccessBlockConfiguration']
    if not all(guard.get(k) for k in ['BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets']):
        raise ValueError('S3 bucket must enforce all four Block Public Access settings')
    if Path(data).is_symlink():raise ValueError('refusing symlink data root')
    root=Path(data).resolve()
    if root==Path(root.anchor):raise ValueError('data root cannot be a filesystem root')
    pointer=prefix.rstrip('/')+'/latest.json'
    if restore:
        try:meta=json.loads(client.get_object(Bucket=bucket,Key=pointer)['Body'].read())
        except client.exceptions.NoSuchKey:return 'empty_private_store'
        if not meta['key'].startswith(prefix.rstrip('/')+'/snapshots/'):
            raise ValueError('snapshot pointer left configured prefix')
        blob=client.get_object(Bucket=bucket,Key=meta['key'])['Body'].read()
        if digest(blob)!=meta['sha256']:raise ValueError('snapshot checksum mismatch')
        root.parent.mkdir(parents=True,exist_ok=True)
        staging=Path(tempfile.mkdtemp(prefix='.cta-restore-',dir=root.parent))
        backup=root.parent/('.cta-before-restore-'+uuid.uuid4().hex)
        try:
            with tarfile.open(fileobj=io.BytesIO(blob),mode='r:gz') as archive:
                for m in archive.getmembers():
                    parts=PurePosixPath(m.name)
                    if parts.is_absolute() or '..' in parts.parts or m.issym() or m.islnk() or not (m.isfile() or m.isdir()):
                        raise ValueError('unsafe archive member')
                archive.extractall(staging,filter='data')
            if not (staging/'history.duckdb').is_file():raise ValueError('missing persistent database')
            import duckdb
            db=duckdb.connect(str(staging/'history.duckdb'),read_only=True)
            try:db.execute('SELECT count(*) FROM observation').fetchone()
            finally:db.close()
            if root.is_symlink():raise ValueError('refusing symlink data root')
            if root.exists():root.rename(backup)
            try:staging.rename(root)
            except Exception:
                if backup.exists():backup.rename(root)
                raise
            if backup.exists():shutil.rmtree(backup)
        finally:
            if staging.exists():shutil.rmtree(staging)
        return meta
    if not (root/'history.duckdb').is_file():raise ValueError('persistent database does not exist')
    if (root/'history.duckdb.wal').exists():raise ValueError('close the database before snapshotting')
    import duckdb
    db=duckdb.connect(str(root/'history.duckdb'),read_only=True)
    buf=io.BytesIO()
    try:
        with tarfile.open(fileobj=buf,mode='w:gz') as archive:
            for p in sorted(root.rglob('*')):
                if any(x in ['.cache','__pycache__','.pytest_cache'] for x in p.relative_to(root).parts):continue
                if p.is_symlink():raise ValueError('refusing symlink in private data root')
                if p.is_file():archive.add(p,arcname=p.relative_to(root))
    finally:db.close()
    blob=buf.getvalue()
    key=prefix.rstrip('/')+'/snapshots/'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex+'.tar.gz'
    meta=dict(key=key,sha256=digest(blob),bytes=len(blob))
    if hasattr(client,'upload_fileobj'):
        from boto3.s3.transfer import TransferConfig
        client.upload_fileobj(io.BytesIO(blob),bucket,key,ExtraArgs={'ServerSideEncryption':'AES256'},
          Config=TransferConfig(multipart_chunksize=8*1024*1024,max_concurrency=2))
    else:
        client.put_object(Bucket=bucket,Key=key,Body=blob,ServerSideEncryption='AES256')
    client.put_object(Bucket=bucket,Key=pointer,Body=json.dumps(meta).encode(),ServerSideEncryption='AES256')
    return meta


def verify_restore(data,bucket,prefix,output):
    from .store import Store,digest,now
    from .daily import init_state
    meta=sync(data,bucket,prefix)
    root=Path(data).resolve()
    import duckdb
    db=duckdb.connect(str(root/'history.duckdb'),read_only=True)
    try:expected=db.execute('SELECT count(*) FROM observation').fetchone()[0]
    finally:db.close()
    with tempfile.TemporaryDirectory(prefix='cta-restore-probe-') as directory:
        restored=Path(directory)/'data'
        sync(restored,bucket,prefix,restore=True)
        db=duckdb.connect(str(restored/'history.duckdb'),read_only=True)
        try:actual=db.execute('SELECT count(*) FROM observation').fetchone()[0]
        finally:db.close()
        if expected!=actual:raise ValueError('restored observation count differs')
    proof=dict(kind='private_storage_restore',status='verified',checked_at=now(),
               snapshot_sha256=meta['sha256'],expected_observations=expected,restored_observations=actual,
               method='private_s3_actual_snapshot_restore')
    out=Path(output);out.parent.mkdir(parents=True,exist_ok=True)
    encoded=json.dumps(proof,indent=2);out.write_text(encoded)
    st=Store(data);init_state(st)
    st.db.execute("INSERT INTO operational_check VALUES ('private_storage_restore',current_timestamp,'verified',?,?)",
                   [str(out),digest(encoded.encode())])
    st.export();st.db.close()
    return proof
