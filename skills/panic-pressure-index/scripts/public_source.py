"""Public monthly data downloader, bounded cache and safe extraction; no API key."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = 'https://bundle.assets.ricequant.com/bundles_v4/rqbundle_{}.tar.bz2'
FILES = {'stocks.h5','indexes.h5','instruments.pk','trading_dates.npy','st_stock_days.h5',
         'suspended_days.h5','ex_cum_factor.h5','dividends.h5','split_factor.h5'}
MAX_ARCHIVE = 4 * 1024**3
MAX_EXTRACTED = 16 * 1024**3


def write_json(path, data):
    path=Path(path); tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(path)


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(4*1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def months(day, count=3):
    month=day.year*12+day.month-1
    for i in range(count):
        n=month-i
        yield f'{n//12:04d}{n%12+1:02d}'


def discover(day):
    errors=[]
    for month in months(day):
        url=BASE.format(month)
        try:
            with urlopen(Request(url,method='HEAD',headers={'User-Agent':'panic-pressure-index/2.0'}),timeout=20) as r:
                length=int(r.headers.get('Content-Length','0'))
                if not 0 < length <= MAX_ARCHIVE: raise ValueError('INVALID_REMOTE_SIZE')
                return dict(month=month,url=url,bytes=length,etag=r.headers.get('ETag'),lastModified=r.headers.get('Last-Modified'))
        except HTTPError as e:
            if e.code not in (404,403): raise
            errors.append(str(e.code))
    raise ValueError('NO_PUBLIC_MONTHLY_BUNDLE:'+','.join(errors))


def download(remote, destination, progress=print):
    dest=Path(destination); part=dest.with_suffix('.part')
    for attempt in range(3):
        try:
            # Restart each bounded attempt: do not combine bytes from different vintages.
            h=hashlib.sha256(); count=0; last=time.monotonic()
            with urlopen(Request(remote['url'],headers={'User-Agent':'panic-pressure-index/2.0'}),timeout=30) as r, part.open('wb') as out:
                if remote.get('etag') and r.headers.get('ETag') != remote['etag']:
                    raise ValueError('REMOTE_CHANGED_DURING_DOWNLOAD')
                while True:
                    block=r.read(1024*1024)
                    if not block: break
                    count+=len(block)
                    if count > remote['bytes'] or count>MAX_ARCHIVE: raise ValueError('DOWNLOAD_TOO_LARGE')
                    out.write(block);h.update(block)
                    if time.monotonic()-last>10:
                        progress(f'下载公开数据 {count//1024**2}/{remote["bytes"]//1024**2} MiB');last=time.monotonic()
            if count != remote['bytes']: raise ValueError('TRUNCATED_DOWNLOAD')
            part.replace(dest)
            return dict(remote,sha256=h.hexdigest())
        except (OSError,ValueError):
            if attempt == 2: raise
            time.sleep(1)


def extract(archive, root):
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    found={}; total=0
    with tarfile.open(archive,'r|bz2') as tar:
        for member in tar:
            name=Path(member.name).name
            if name not in FILES: continue
            if not member.isfile() or name in found or '..' in Path(member.name).parts or Path(member.name).is_absolute():
                raise ValueError('UNSAFE_OR_DUPLICATE_ARCHIVE_MEMBER')
            total+=member.size
            if member.size<0 or total>MAX_EXTRACTED: raise ValueError('ARCHIVE_TOO_LARGE')
            target=root/name
            if target.exists(): raise ValueError('EXTRACTION_DESTINATION_EXISTS')
            h=hashlib.sha256(); count=0
            with tar.extractfile(member) as stream, target.open('xb') as out:
                for block in iter(lambda:stream.read(4*1024*1024),b''):
                    count+=len(block); out.write(block);h.update(block)
            if count != member.size: raise ValueError('TRUNCATED_ARCHIVE_MEMBER')
            found[name]=dict(bytes=count,sha256=h.hexdigest())
    if set(found)!=FILES: raise ValueError('MISSING_BUNDLE_FILES:'+','.join(sorted(FILES-set(found))))
    write_json(root/'manifest.json',found)
    return found


def acquire(cache, day, *, offline=False, progress=print):
    cache=Path(cache); cache.mkdir(parents=True,exist_ok=True)
    pointer=cache/'latest.json'
    existing=json.loads(pointer.read_text()) if pointer.exists() else None
    if offline:
        if not existing: raise ValueError('NO_OFFLINE_CACHE')
        return cache/existing['directory'],dict(existing,networkChecked=False)
    remote=discover(day)
    # Check upstream every invocation; immutable ETag is the cache key, never the month alone.
    key=hashlib.sha256(json.dumps(remote,sort_keys=True).encode()).hexdigest()[:20]
    directory='bundle-'+remote['month']+'-'+key
    root=cache/directory
    if (root/'manifest.json').exists():
        info=json.loads((root/'download.json').read_text())
        info.update(directory=directory,networkChecked=True)
        write_json(pointer,info)
        return root,info
    progress(f'公开数据首次初始化：约 {remote["bytes"]//1024**2} MiB 下载；需数GB缓存空间。')
    if shutil.disk_usage(cache).free < remote['bytes']*5: raise ValueError('INSUFFICIENT_DISK_SPACE')
    archive=cache/(directory+'.tar.bz2')
    info=download(remote,archive,progress)
    staging=cache/(directory+'.extracting')
    if staging.exists(): shutil.rmtree(staging)
    try:
        extract(archive,staging)
        info.update(directory=directory,networkChecked=True)
        write_json(staging/'download.json',info)
        staging.replace(root);write_json(pointer,info)
        archive.unlink()
    except Exception:
        # No pointer update until extraction completed. A failed archive stays resumable by retry.
        raise
    return root,info
