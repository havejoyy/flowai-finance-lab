"""Validated SQLite public-data batches. No credentials, models or background schedule."""
import copy
import json
import math
import threading
import uuid
from datetime import datetime, timedelta, timezone
from engine import CN, digest, encode, timestamp
from screening_data import fetch_snapshot, fetch_history, validate_snapshot_dates, repair_snapshot_gaps
from data_recovery import retry_data
from industry_data import fetch_industries, validate_industries, attach_industries

ADAPTER = 'managed-public-v1'
SNAPSHOT_TTL = 15 * 60
HISTORY_TTL = 6 * 3600
INDUSTRY_TTL = 24 * 3600


def validate_history(history, as_of, stock=None):
    rows = history.get('rows')
    if not isinstance(rows,list) or len(rows)<60:
        raise ValueError('历史不足60期')
    days = [r['date'] for r in rows]
    if days!=sorted(set(days)) or days[-1]!=as_of or any(d>as_of for d in days):
        raise ValueError('历史日期重复、超出截止日或未到目标日')
    for r in rows:
        datetime.strptime(r['date'],'%Y-%m-%d')
        if any(isinstance(r.get(k),bool) or not isinstance(r.get(k),(int,float)) or not math.isfinite(r[k]) for k in ('close','amount','pct')) or r['close']<=0 or r['amount']<=0:
            raise ValueError('历史字段缺失或数值无效')
    if stock:
        r=rows[-1]
        if abs(r['pct']-stock['pct'])>.15 or abs(r['amount']-stock['amount'])/stock['amount']>.03:
            raise ValueError('快照与日K涨幅或成交额不一致')
    return history


def validate_snapshot(snapshot, now):
    if snapshot.get('data_kind')!='real' or not snapshot.get('source') or not isinstance(snapshot.get('rows'),list) or not snapshot['rows']:
        raise ValueError('缓存只接受真实且非空的行情')
    seen=set()
    for r in snapshot['rows']:
        key=(r.get('market'),r.get('code'))
        if key in seen or key[0] not in (0,1) or not isinstance(key[1],str) or len(key[1])!=6 or not key[1].isdigit():
            raise ValueError('行情证券代码重复或无效')
        seen.add(key)
    date=validate_snapshot_dates(snapshot['rows'],now)
    if date!=snapshot.get('as_of'):
        raise ValueError('快照日期与行情不一致')
    return snapshot


class MarketStore:
    def __init__(self,store,run_id='',progress=lambda msg:None,force=False,now=None):
        self.store=store;self.run_id=run_id;self.progress=progress;self.force=force
        self.clock=now or (lambda:datetime.now(timezone.utc))
        self.lock=threading.Lock()
        self.stats=dict(snapshot_reused=0,snapshot_fetched=0,history_reused=0,history_fetched=0,
                        incremental=0,full_refresh=0,invalid_cache=0,failed=0,industry_reused=0,industry_fetched=0)
        self.refs={}
        self.recovery_events=[]
        self.stats['automatic_retries']=0
        with store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS market_batches(
            id TEXT PRIMARY KEY,kind TEXT NOT NULL,cache_key TEXT NOT NULL,as_of TEXT NOT NULL,
            adapter TEXT NOT NULL,checked_at TEXT NOT NULL,hash TEXT NOT NULL,body TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS market_lookup ON market_batches(kind,cache_key,as_of);
            CREATE TABLE IF NOT EXISTS market_heads(kind TEXT,cache_key TEXT,as_of TEXT,batch_id TEXT NOT NULL,
            PRIMARY KEY(kind,cache_key,as_of));
            CREATE TABLE IF NOT EXISTS market_bars(batch_id TEXT,code TEXT,day TEXT,close REAL,amount REAL,pct REAL,
            PRIMARY KEY(batch_id,code,day));
            CREATE TABLE IF NOT EXISTS market_industry_members(batch_id TEXT,code TEXT,industry_code TEXT,industry_name TEXT,begin_day TEXT,PRIMARY KEY(batch_id,code,industry_code));
            CREATE TABLE IF NOT EXISTS market_quote_checks(day TEXT,code TEXT,disposition TEXT,reason TEXT,source TEXT,
            PRIMARY KEY(day,code));
            CREATE TABLE IF NOT EXISTS market_issues(id TEXT PRIMARY KEY,run_id TEXT,code TEXT,kind TEXT,reason TEXT,created_at TEXT);''')
    def retry(self,call,label):
        def notify(event):
            with self.lock:
                self.recovery_events.append(event)
                self.stats['automatic_retries']+=1
            self.progress('自动重试 '+label+'：第%d/%d次，%d秒后继续'%(event['attempt'],event['max_attempts'],event['delay_seconds']))
        return retry_data(call,label,notify)
    def tick(self,key,msg):
        with self.lock:self.stats[key]+=1
        self.progress(msg)
    def issue(self,code,kind,reason):
        with self.store.connect() as db:
            db.execute('INSERT INTO market_issues VALUES(?,?,?,?,?,?)',(str(uuid.uuid4()),self.run_id,code,kind,reason,timestamp()))
    def cached(self,kind,key,as_of=None,ttl=None):
        with self.store.connect() as db:
            sql='SELECT b.* FROM market_heads h JOIN market_batches b ON b.id=h.batch_id WHERE h.kind=? AND h.cache_key=? AND b.adapter=?'
            args=[kind,key,ADAPTER]
            if as_of is not None:sql+=' AND h.as_of=?';args.append(as_of)
            sql+=' ORDER BY b.as_of DESC,b.checked_at DESC LIMIT 1'
            row=db.execute(sql,args).fetchone()
        if not row:return None
        try:
            checked=datetime.fromisoformat(row['checked_at']);age=(self.clock()-checked).total_seconds()
            if age<0 or (ttl is not None and age>ttl):return None
            body=json.loads(row['body'])
            if digest(body)!=row['hash']:raise ValueError('本地数据指纹不匹配')
            return dict(id=row['id'],as_of=row['as_of'],body=body,checked_at=row['checked_at'])
        except (ValueError,TypeError):
            self.tick('invalid_cache','本地数据校验未通过，准备重新获取');return None
    def publish(self,kind,key,as_of,body):
        identifier=str(uuid.uuid4());encoded=encode(body);hashed=digest(body)
        with self.store.connect() as db:
            db.execute('INSERT INTO market_batches VALUES(?,?,?,?,?,?,?,?)',(identifier,kind,key,as_of,ADAPTER,self.clock().isoformat(),hashed,encoded))
            if kind=='history':
                db.executemany('INSERT INTO market_bars VALUES(?,?,?,?,?,?)',[(identifier,key,r['date'],r['close'],r['amount'],r['pct']) for r in body['rows']])
            if kind=='industry':
                db.executemany('INSERT INTO market_industry_members VALUES(?,?,?,?,?)',[(identifier,c,i,g['name'],r['begin']) for i,g in body['groups'].items() for c,r in g['members'].items()])
            db.execute('INSERT OR REPLACE INTO market_heads VALUES(?,?,?,?)',(kind,key,as_of,identifier))
        return identifier
    def snapshot(self,fetcher=None,refresh=False):
        cached=None if self.force or refresh else self.cached('snapshot','universe',ttl=SNAPSHOT_TTL)
        if cached and datetime.fromisoformat(cached['checked_at']).astimezone(CN).date()==self.clock().astimezone(CN).date():
            try:
                result=validate_snapshot(copy.deepcopy(cached['body']),self.clock().astimezone(CN))
                self.refs['snapshot']=cached['id'];self.tick('snapshot_reused','检查本地：复用已校验行情，日期 '+result['as_of']);return result
            except (ValueError,KeyError,TypeError):self.tick('invalid_cache','本地快照过期或无效，重新获取')
        result=self.retry(fetcher or (lambda:fetch_snapshot(self.progress)),'收盘快照')
        if fetcher is None:result=repair_snapshot_gaps(result,self.progress)
        validate_snapshot(result,self.clock().astimezone(CN))
        self.refs['snapshot']=self.publish('snapshot','universe',result['as_of'],result)
        self.tick('snapshot_fetched','同日行情已校验并保存，日期 '+result['as_of'])
        return copy.deepcopy(result)
    def industry(self,snapshot,fetcher=None,refresh=False):
        cached=None if self.force or refresh else self.cached('industry','sw-level1',ttl=INDUSTRY_TTL)
        if cached:
            try:
                validate_industries(cached['body'])
                if cached['body'].get('failed_groups'):cached=None
            except (ValueError,KeyError,TypeError):
                self.tick('invalid_cache','行业分类缓存无效，重新获取');cached=None
        if cached:
            data=copy.deepcopy(cached['body']);identifier=cached['id']
            self.tick('industry_reused','复用已校验申万行业分类')
        else:
            try:
                data=self.retry(fetcher or (lambda:fetch_industries(self.progress)),'行业分类')
                validate_industries(data)
                identifier=self.publish('industry','sw-level1',data['observed_date'],data)
                self.tick('industry_fetched','行业分类已校验并保存')
                for code,issue in data.get('failed_groups',{}).items():self.issue(code,'industry',issue['reason'])
            except Exception as exc:
                reason=str(exc) if isinstance(exc,ValueError) else '行业分类获取或校验失败'
                self.issue('','industry',reason);self.tick('failed','行业分类获取失败')
                raise ValueError(reason) from None
        self.refs['industry']=identifier
        return attach_industries(snapshot,data,identifier)
    def history(self,stock,as_of,fetcher=fetch_history):
        key='%s.%s'%(stock['market'],stock['code'])
        cached=None if self.force else self.cached('history',key,as_of,HISTORY_TTL)
        if cached:
            try:
                result=validate_history(copy.deepcopy(cached['body']),as_of,stock)
                with self.lock:self.refs[key]=cached['id']
                self.tick('history_reused','复用历史 '+stock['code']);return result
            except (ValueError,TypeError,KeyError):self.tick('invalid_cache','历史与本轮数据不符，重新核验 '+stock['code'])
        try:
            previous=None if self.force else self.cached('history',key)
            result=None
            if previous and previous['as_of']<as_of and (datetime.fromisoformat(as_of)-datetime.fromisoformat(previous['as_of'])).days<=30:
                try:
                    validate_history(previous['body'],previous['as_of'])
                    start=(datetime.fromisoformat(previous['as_of'])-timedelta(days=12)).date().isoformat()
                    delta=self.retry(lambda:fetcher(stock,as_of,start=start),'增量日K '+stock['code'])
                    old={r['date']:r for r in previous['body']['rows']};new={r['date']:r for r in delta['rows']}
                    if len(new)!=len(delta['rows']) or previous['as_of'] not in new or delta['basis']!=previous['body']['basis']:
                        raise ValueError('增量缺少重叠期或来源口径变化')
                    for day in old.keys() & new.keys():
                        if any(abs(old[day][k]-new[day][k])>max(1e-7,abs(old[day][k])*1e-8) for k in ('close','amount','pct')):
                            raise ValueError('历史或复权资料修订')
                    old_factor=previous['body'].get('evidence',{}).get('sina_qfq',{}).get('raw')
                    new_factor=delta.get('evidence',{}).get('sina_qfq',{}).get('raw')
                    if old_factor and (not new_factor or self.factor_hash(old_factor)!=self.factor_hash(new_factor)):
                        raise ValueError('复权因子发生变化')
                    merged=dict(old,**new)
                    result=dict(delta,rows=[merged[d] for d in sorted(merged)][-90:],
                                evidence={'delta':delta.get('evidence',{}),'base_batch_id':previous['id']},update_mode='incremental')
                    # Preserve complete factor provenance for the next update.
                    if new_factor:result['evidence']['sina_qfq']=delta['evidence']['sina_qfq']
                    validate_history(result,as_of,stock)
                    self.tick('incremental','增量补齐并核验 '+stock['code'])
                except (ValueError,TypeError,KeyError):
                    result=None
            if result is None:
                result=self.retry(lambda:fetcher(stock,as_of),'历史日K '+stock['code'])
                validate_history(result,as_of,stock)
                self.tick('full_refresh','完整历史已核验 '+stock['code'])
            identifier=self.publish('history',key,as_of,result)
            with self.lock:self.refs[key]=identifier
            self.tick('history_fetched','历史已保存 '+stock['code'])
            return copy.deepcopy(result)
        except Exception as exc:
            reason=str(exc) if isinstance(exc,ValueError) else '历史获取或本地保存失败'
            self.issue(stock['code'],'history',reason);self.tick('failed','历史待核验 '+stock['code'])
            raise ValueError(reason) from None
    @staticmethod
    def factor_hash(raw):
        # Signature comments can change independently; compare factor JSON only.
        return digest(json.JSONDecoder().raw_decode(raw.split('=',1)[1].lstrip())[0])
    def table(self,query='',state='all',page=1,limit=50,date='',kind='all'):
        """All locally saved dates, latest batch per type/stock/day; read-only, no network."""
        if not isinstance(query,str) or len(query)>80 or state not in ('all','missing','excluded') or kind not in ('all','snapshot','history'):
            raise ValueError('数据查询条件无效')
        if date:
            try:
                if datetime.strptime(date,'%Y-%m-%d').strftime('%Y-%m-%d')!=date:raise ValueError()
            except (ValueError,TypeError):raise ValueError('日期无效') from None
        try:page=int(page);limit=int(limit)
        except (ValueError,TypeError):raise ValueError('页码无效') from None
        if page<1 or not 1<=limit<=100:raise ValueError('页码或每页数量无效')
        rows,as_of,checked,classification_date=self.table_records()
        values=[r for r in rows.values() if kind=='all' or r['kind']==kind];dates=sorted({r['date'] for r in values},reverse=True);q=query.strip().casefold()
        values=[r for r in values if (not q or q in (r['code']+' '+r['name']+' '+r['industry']).casefold()) and (not date or r['date']==date) and (kind=='all' or r['kind']==kind) and (state=='all' or r['status']==('行情缺失' if state=='missing' else '基础排除'))]
        values.sort(key=lambda r:(r['code'],r['kind']!='snapshot'));values.sort(key=lambda r:r['date'],reverse=True)
        total=len(values);pages=max(1,math.ceil(total/limit));page=min(page,pages)
        return dict(rows=values[(page-1)*limit:page*limit],total=total,page=page,pages=pages,limit=limit,as_of=as_of,checked_at=checked,classification_date=classification_date,dates=dates,date_from=dates[-1] if dates else '',date_to=dates[0] if dates else '',date_count=len(dates),date=date,kind=kind)

    def quote_checks(self, day):
        with self.store.connect() as db:
            rows = db.execute("SELECT code,disposition,reason,source FROM market_quote_checks WHERE day=?", (day,))
            return {r['code']: dict(r) for r in rows}

    def table_records(self):
        """Materialize the same dated rows for the table and the owner-only publisher."""
        with self.store.connect() as db:
            snapshots=db.execute("SELECT b.id,b.body,b.checked_at FROM market_heads h JOIN market_batches b ON h.batch_id=b.id WHERE h.kind='snapshot' AND b.adapter=? ORDER BY b.checked_at,b.id",(ADAPTER,)).fetchall()
            history_batches=db.execute("SELECT b.id,b.cache_key,b.as_of,b.checked_at,b.body,COUNT(k.day) observations FROM market_heads h JOIN market_batches b ON h.batch_id=b.id LEFT JOIN market_bars k ON k.batch_id=b.id WHERE h.kind='history' AND b.adapter=? GROUP BY b.id ORDER BY b.checked_at,b.id",(ADAPTER,)).fetchall()
            bars=db.execute("SELECT k.*,b.checked_at,b.as_of FROM market_heads h JOIN market_batches b ON h.batch_id=b.id JOIN market_bars k ON k.batch_id=b.id WHERE h.kind='history' AND b.adapter=? ORDER BY b.checked_at,b.id",(ADAPTER,)).fetchall()
        rows={};names={};industries={};histories={};batches={};checked='';as_of='';classification_date=''
        industry=self.cached('industry','sw-level1',ttl=INDUSTRY_TTL)
        if industry:classification_date=industry['body'].get('observed_date','')
        for h in history_batches:
            body=json.loads(h['body']);batches[h['id']]=body.get('basis','口径未知')
            histories[h['cache_key']]=dict(as_of=h['as_of'],observations=h['observations'])
            checked=max(checked,h['checked_at']);as_of=max(as_of,h['as_of'])
        for head in snapshots:
            snapshot=json.loads(head['body']);checked=max(checked,head['checked_at']);as_of=max(as_of,snapshot['as_of'])
            if industry:
                try:snapshot=attach_industries(snapshot,industry['body'],industry['id']);classification_date=snapshot['industry_data']['observed_date']
                except (ValueError,KeyError,TypeError):pass
            checks = self.quote_checks(snapshot['as_of'])
            for r in snapshot['rows']:
                key='%s.%s'%(r['market'],r['code']);names[key]=r['name'];industries[key]=r.get('industry') or '行业未知'
                h=histories.get(key,{})
                excluded='ST' in r['name'].upper() or '退' in r['name']
                missing=not r.get('eod') or r.get('date')!=snapshot['as_of'] or any(r.get(k) is None for k in ('close','pct','amount')) or (r.get('close') or 0)<=0 or (r.get('amount') or 0)<=0
                rows[('snapshot',key,snapshot['as_of'])]=dict(code=r['code'],name=r['name'],industry=industries[key],close=r.get('close') if (r.get('close') or 0)>0 else None,pct=r.get('pct') if (r.get('close') or 0)>0 else None,amount=r.get('amount') if (r.get('amount') or 0)>0 else None,date=snapshot['as_of'],history_date=h.get('as_of',''),history_count=h.get('observations',0),status='基础排除' if excluded else '行情缺失' if missing else '已保存',kind='snapshot',basis='不复权（收盘快照）')
                check = checks.get(r['code'])
                if missing and check:
                    record = rows[('snapshot',key,snapshot['as_of'])]
                    if check['disposition'] == 'no_trade':
                        record['status'] = '基础排除'
                    record['basis'] = check['reason'] + '（' + check['source'] + '）'
        if industry:
            # These labels describe current classification, not historical membership.
            membership={}
            for group in industry['body']['groups'].values():
                for code,member in group['members'].items():membership.setdefault(code,[]).append((group['name'],member['name']))
        else:membership={}
        for r in bars:
            key=r['code'];code=key.split('.')[-1];options=membership.get(code,[])
            name=names.get(key) or (options[0][1] if len(options)==1 else '名称未知')
            label=industries.get(key) or (options[0][0] if len(options)==1 else '行业未知')
            excluded='ST' in name.upper() or '退' in name
            h=histories.get(key,{})
            rows[('history',key,r['day'])]=dict(code=code,name=name,industry=label,close=r['close'],pct=r['pct'],amount=r['amount'],date=r['day'],history_date=h.get('as_of',''),history_count=h.get('observations',0),status='基础排除' if excluded else '已保存',kind='history',basis=batches.get(r['batch_id'],'口径未知'))
        return rows,as_of,checked,classification_date

    def status(self):
        with self.store.connect() as db:
            row=db.execute("SELECT b.as_of,b.checked_at,b.body FROM market_heads h JOIN market_batches b ON h.batch_id=b.id WHERE h.kind='snapshot' AND b.adapter=? ORDER BY b.as_of DESC,b.checked_at DESC LIMIT 1",(ADAPTER,)).fetchone()
            count=db.execute('SELECT COUNT(*) FROM market_heads WHERE kind=?',('history',)).fetchone()[0]
            resources=[dict(r) for r in db.execute("SELECT b.kind,b.cache_key,b.as_of,b.checked_at FROM market_heads h JOIN market_batches b ON h.batch_id=b.id WHERE h.kind LIKE 'five.%' ORDER BY b.kind,b.as_of DESC,b.cache_key")]
        snapshot=json.loads(row['body']) if row else {}
        industry=self.cached('industry','sw-level1',ttl=INDUSTRY_TTL)
        meta=None
        if industry and snapshot:
            try:meta=attach_industries(snapshot,industry['body'],industry['id'])['industry_data']
            except (ValueError,KeyError,TypeError):pass
        checks = self.quote_checks(snapshot.get('as_of',''))
        no_trade = {code for code, check in checks.items() if check['disposition'] == 'no_trade'}
        return dict(as_of=row['as_of'] if row else '',checked_at=row['checked_at'] if row else '',source=snapshot.get('source',''),
                    quote_missing=sum(not r.get('eod') or r.get('close') is None or r.get('pct') is None or r.get('amount') is None or r.get('amount',0)<=0 for r in snapshot.get('rows',[]) if r['code'] not in no_trade and 'ST' not in r.get('name','').upper() and '退' not in r.get('name','')),industry_data=({k:v for k,v in meta.items() if k not in ('unknown','conflicts')} if meta else None),warnings=snapshot.get('warnings',[]),industry_available=bool(meta and meta['matched']) or any(r.get('industry') not in ('行业未知','-','',None) for r in snapshot.get('rows',[])),history_entries=count,sqlite=True,five_resources=resources,
                    message='本地日期仅表示来源快照；未接交易日历，不宣称已确认最近交易日。')
