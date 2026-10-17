"""Read-only RQAlpha public bundle evidence, no rqalpha/rqdatac runtime.

Never execute downloaded pickle classes or use extractall on an archive.
Do not treat monthly archives as current-day feeds or historical publication.
"""
from datetime import date, datetime, timedelta
import hashlib
from bisect import bisect_right
from decimal import Decimal
import io
import json
from pathlib import Path
import pickle
import shutil
import tarfile

FILES={'stocks.h5','indexes.h5','instruments.pk','trading_dates.npy',
       'st_stock_days.h5','suspended_days.h5','ex_cum_factor.h5',
       'dividends.h5','split_factor.h5'}


class MetadataUnpickler(pickle.Unpickler):
    def find_class(self,module,name):
        allowed={('datetime','datetime'):datetime,('datetime','date'):date,
                 ('datetime','timedelta'):timedelta}
        if (module,name) in allowed:return allowed[module,name]
        raise ValueError('UNSAFE_METADATA_CLASS')


def read_instruments(path):
    with open(path,'rb') as stream:
        value=MetadataUnpickler(stream).load()
    if not isinstance(value,list) or any(not isinstance(row,dict) for row in value):
        raise ValueError('INVALID_INSTRUMENT_METADATA')
    return value


def extract_evidence(archive,output):
    root=Path(output);root.mkdir(parents=True,exist_ok=True)
    found={}
    with tarfile.open(archive,'r|bz2') as tar:
        for member in tar:
            name=Path(member.name).name
            if name not in FILES:continue
            if not member.isfile() or name in found or member.size>8*1024**3:
                raise ValueError('UNSAFE_OR_DUPLICATE_ARCHIVE_MEMBER')
            target=root/name
            if target.exists():raise ValueError('EVIDENCE_ALREADY_EXISTS')
            stream=tar.extractfile(member)
            temp=root/(name+'.part')
            with temp.open('xb') as out:shutil.copyfileobj(stream,out,1024*1024)
            if temp.stat().st_size!=member.size:raise ValueError('TRUNCATED_BUNDLE')
            temp.replace(target);found[name]=member.size
    missing=FILES-set(found)
    if missing:raise ValueError('MISSING_BUNDLE_FILES:'+','.join(sorted(missing)))
    return found


def day_string(value):
    digits=str(int(value))[:8]
    return datetime.strptime(digits,'%Y%m%d').date().isoformat()


def verify_manifest(root, required=None):
    """Verify every pinned byte before loading source data. Not supplier attestation."""
    root=Path(root); required=FILES if required is None else required
    manifest=json.loads((root/'manifest.json').read_text())
    for name in sorted(required):
        if name not in manifest:raise ValueError('MISSING_MANIFEST_ENTRY')
        item=manifest[name];path=root/name
        if path.is_symlink() or path.stat().st_size!=item['bytes']:
            raise ValueError('BUNDLE_SIZE_MISMATCH')
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
        if digest.hexdigest()!=item['sha256']:raise ValueError('BUNDLE_HASH_MISMATCH')
    return hashlib.sha256(json.dumps({k:manifest[k] for k in sorted(required)},sort_keys=True).encode()).hexdigest()


def adjustment_factor(handles, code, timestamp):
    """RQ sparse-factor semantics, guarded by independent corporate-action files.

    Identity is accepted only for an absent (never empty) factor dataset with
    no dividend/split dataset in a verified archive. Conflicting evidence stays
    missing. This source-specific rule is never applied to other providers.
    """
    factors=handles['ex_cum_factor.h5']
    if code not in factors:
        if (code in handles['dividends.h5'] or code in handles['split_factor.h5']):
            return None,'CONFLICTING_CORPORATE_ACTION'
        return Decimal(1),'RQ_SPARSE_NO_ACTION_V1'
    rows=factors[code][:]
    dates=[int(r['start_date']) for r in rows]
    if not dates or dates!=sorted(set(dates)):return None,'INVALID_FACTOR_TIMELINE'
    pos=bisect_right(dates,timestamp)-1
    if pos<0:return None,'FACTOR_NOT_EFFECTIVE'
    value=Decimal(str(rows[pos]['ex_cum_factor']))
    if not value.is_finite() or value<=0:return None,'INVALID_FACTOR'
    return value,'RQ_EX_CUM_FACTOR'


class RQBundle:
    def __init__(self,path):
        import numpy as np
        import h5py
        self.root=Path(path)
        self.manifest_hash=verify_manifest(self.root)
        self.instruments=read_instruments(self.root/'instruments.pk')
        self.calendar=[day_string(v) for v in np.load(self.root/'trading_dates.npy',allow_pickle=False)]
        if self.calendar!=sorted(set(self.calendar)):raise ValueError('INVALID_BUNDLE_CALENDAR')
        self.handles={name:h5py.File(self.root/name,'r') for name in FILES if name.endswith('.h5')}
    def close(self):
        for f in self.handles.values():f.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def inspect(self):
        result={'files':{},'instrumentCount':len(self.instruments),'calendarStart':self.calendar[0],
                'calendarEnd':self.calendar[-1],'instrumentFields':sorted(set().union(*(x.keys() for x in self.instruments)))}
        for name,handle in self.handles.items():
            keys=list(handle.keys()); item={'instruments':len(keys)}
            if keys:
                dataset=handle[keys[0]]
                item.update(sampleKey=keys[0],fields=list(dataset.dtype.names or []),sampleRows=len(dataset))
                if len(dataset):item['sampleFirst']=str(dataset[0]);item['sampleLast']=str(dataset[-1])
            result['files'][name]=item
        return result
