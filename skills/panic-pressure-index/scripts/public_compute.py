"""Build all six raw factors from public bundle bytes; no private app dependency."""
from bisect import bisect_left, bisect_right
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from bundle_reader import RQBundle,day_string,adjustment_factor
from public_methodology import METHOD,METHOD_HASH,engine
from public_source import write_json
from panic_pressure import digest


def select_universe(instruments, anchor):
    selected=[]
    for m in instruments:
        code=m.get('order_book_id','')
        if (m.get('type')!='CS' or m.get('exchange') not in ('XSHG','XSHE') or
            not re.fullmatch(r'(?:60[0135]\d{3}\.XSHG|688\d{3}\.XSHG|00[0123]\d{3}\.XSHE|30[012]\d{3}\.XSHE)',code)):
            continue
        # A deliberately fixed current-survivor cohort; do not label as a dated universe.
        if (m.get('status')!='Active' or m.get('de_listed_date')!='0000-00-00' or
                m.get('special_type') not in ('Normal','ST','StarST') or
                '退' in m.get('symbol','') or m.get('listed_date','9999-99-99')>anchor):
            continue
        selected.append(m)
    if len(selected)<1000 or len({m['order_book_id'] for m in selected})!=len(selected):
        raise ValueError('PUBLIC_UNIVERSE_INVALID')
    return sorted(selected,key=lambda m:m['order_book_id'])


def cache_read(path, expected):
    if not path.exists(): return None
    value=json.loads(path.read_text())
    if value.get('contentHash')!=digest({k:v for k,v in value.items() if k!='contentHash'}):
        raise ValueError('RAW_CACHE_CORRUPTED')
    if value.get('scope')!=expected: raise ValueError('RAW_CACHE_SCOPE_MISMATCH')
    return value


def prepare(bundle_path, work, *, progress=print):
    work=Path(work); work.mkdir(parents=True,exist_ok=True)
    with RQBundle(bundle_path) as b:
        index=b.handles['indexes.h5']['000300.XSHG'][:]
        if not len(index): raise ValueError('EMPTY_BENCHMARK')
        anchor=day_string(index[-1]['datetime'])
        calendar=b.calendar
        end=calendar.index(anchor)+1
        start=max(61,end-METHOD['initializationRawSessions'])
        days=calendar[start:end]
        if len(days)<METHOD['minimumReferences']+1: raise ValueError('INSUFFICIENT_PUBLIC_HISTORY')
        metas=select_universe(b.instruments,anchor)
        universe_hash=digest(metas)
        scope=dict(methodHash=METHOD_HASH,bundleHash=b.manifest_hash,universeHash=universe_hash,
                   start=days[0],end=anchor)
        key=digest(scope)
        cache_file=work/(key+'.json')
        existing=cache_read(cache_file,scope)
        if existing: return existing,calendar
        progress(f'开始计算：{len(metas)}只存续股票，{len(days)}个交易日；六因子逐日重算。')
        pre_start=calendar[start-60]
        stock=b.handles['stocks.h5'];factors=b.handles['ex_cum_factor.h5']
        sts=b.handles['st_stock_days.h5'];susps=b.handles['suspended_days.h5']
        prepared=[]
        for i,meta in enumerate(metas):
            code=meta['order_book_id']
            board={'MainBoard':'MAIN','SME':'MAIN','GEM':'CHINEXT','KSH':'STAR'}.get(meta.get('board_type'))
            if not board or code not in stock or code not in sts: raise ValueError('MISSING_SECURITY_DATA:'+code)
            st_days={day_string(v) for v in sts[code][:]}
            sus_days={day_string(v) for v in susps[code][:]} if code in susps else set()
            raw=stock[code][:]
            raw=raw[(raw['datetime']>=int(pre_start.replace('-','')+'000000')) &
                    (raw['datetime']<=int(anchor.replace('-','')+'000000'))]
            fr=factors[code][:] if code in factors else []
            fd=[int(r['start_date']) for r in fr]
            if fd!=sorted(set(fd)): raise ValueError('INVALID_FACTOR_TIMELINE')
            identity,_=adjustment_factor(b.handles,code,0) if code not in factors else (None,None)
            history={};limits={}
            for row in raw:
                d=day_string(row['datetime']);pos=bisect_right(fd,int(row['datetime']))-1
                factor=Decimal(str(fr[pos]['ex_cum_factor'])) if pos>=0 else identity
                if d in history: raise ValueError('DUPLICATE_PRICE_DATE')
                history[d]=dict(close=str(row['close']),preClose=str(row['prev_close']),
                                factor=str(factor) if factor is not None else None,suspended=d in sus_days)
                limits[d]=(str(row['limit_down']),str(row['limit_up']))
            prepared.append((meta,board,history,limits,st_days,sus_days))
        benchmark={day_string(r['datetime']):str(r['close']) for r in index}
        rows=[]
        checkpoint=work/(key+'.checkpoint.json')
        previous=cache_read(checkpoint,scope)
        if previous:
            rows=previous['rows']
            if [r['tradeDate'] for r in rows]!=days[:len(rows)]: raise ValueError('CHECKPOINT_DATE_MISMATCH')
        for i,target in enumerate(days[len(rows):],start=len(rows)):
            securities=[]
            for meta,board,history,limits,st_days,sus_days in prepared:
                code=meta['order_book_id'];listed=meta['listed_date'];lo,hi=limits.get(target,(None,None))
                regime='LIMITED' if lo is not None and hi is not None and Decimal(lo)>0 and Decimal(hi)>0 else 'UNKNOWN'
                securities.append(dict(securityId=code,tradeDate=target,stateVerified=True,
                    exchange='SSE' if code.endswith('XSHG') else 'SZSE',assetType='A_SHARE',board=board,
                    listed=listed<=target,listedSessions=max(0,bisect_right(calendar,target)-bisect_left(calendar,listed)),
                    st=target in st_days,suspended=target in sus_days,delisting=False,
                    regime=regime,downLimit=lo,tickSize='0.01',history=history))
            raw=engine.calculate_raw(target,calendar,benchmark,securities,universe_verified=True)
            raw.update(id=digest(dict(scope=scope,day=target)))
            rows.append(json.loads(json.dumps(raw,default=str)))
            if (i+1)%10==0 or i+1==len(days):
                progress(f'计算 {i+1}/{len(days)}：{target} {raw["status"]}')
                partial=dict(scope=scope,rows=rows,universeCount=len(metas))
                partial['contentHash']=digest(partial);write_json(checkpoint,partial)
        result=dict(scope=scope,rows=rows,universeCount=len(metas))
        result['contentHash']=digest(result);write_json(cache_file,result)
        checkpoint.unlink(missing_ok=True)
        return result,calendar


def score(prepared,calendar,target,checked_on,source):
    rows=prepared['rows'];anchor=prepared['scope']['end']
    if target!='latest':
        date.fromisoformat(target)
        if target>checked_on: raise ValueError('FUTURE_DATE_NOT_ALLOWED')
        candidates=[r for r in rows if r['tradeDate']==target]
    else:
        candidates=[r for r in reversed(rows) if r['tradeDate']<=checked_on]
    if not candidates: raise ValueError('REQUESTED_DATE_UNAVAILABLE:'+target+';latestData='+anchor)
    result=None
    for raw in candidates:
        result=engine.calculate_score(raw,calendar,rows)
        if result['status']=='READY' or target!='latest': break
    if result['status']!='READY': result.update(score=None,scores={},dimensions={})
    scope=prepared['scope']
    result.update(requestedDate=target,checkedOn=checked_on,latestSnapshotDate=anchor,
        dataAgeCalendarDays=(date.fromisoformat(checked_on)-date.fromisoformat(result['tradeDate'])).days,
        methodVersion=METHOD['version'],universeVersion=METHOD['universeVersion'],
        universeCount=prepared['universeCount'],universeHash=scope['universeHash'],
        sourceVersion='RQ_PUBLIC_'+source['month'],sourceUrl=source['url'],
        sourceArchiveHash=source['sha256'],sourceNetworkChecked=source.get('networkChecked',False),
        historyMode=METHOD['historyMode'],methodHash=METHOD_HASH,limitations=METHOD['limitations'],
        inputManifestHash=digest(scope),sourceContentHash=prepared['contentHash'],
        recomputed=True,calculationScope='PUBLIC_PRICES_TO_SIX_FACTORS_TO_SCORE')
    result['resultHash']=digest({k:result[k] for k in ('tradeDate','score','scores','dimensions','methodHash','inputManifestHash','sourceContentHash','referenceCounts')})
    return result
