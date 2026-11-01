import copy,tempfile,unittest,uuid
from datetime import datetime,timedelta,timezone
from unittest.mock import Mock
from engine import Store,CN,digest
from market_store import MarketStore,validate_history
from screening import ScreeningStore
from screening_data import demo_snapshot,demo_history

def fixtures(day='2026-10-30'):
    s=demo_snapshot();s.update(as_of=day,data_kind='real',source='测试夹具（非实测行情）')
    for i,r in enumerate(s['rows']):r.update(code='%06d'%(600000+i),date=day,quote_at=day+'T15:30:00+08:00',eod=True)
    return s

def hist(stock,day,start=None):return demo_history(stock,day)

class MarketTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=Store(self.tmp.name)
        self.clock=datetime(2026,10,31,8,tzinfo=timezone.utc);self.m=MarketStore(self.store,now=lambda:self.clock)
        self.s=fixtures();self.stock=self.s['rows'][0]
    def test_snapshot_reuse_expiry_force_and_invalid(self):
        f=Mock(return_value=self.s);self.m.snapshot(f);self.m.snapshot(f);self.assertEqual(f.call_count,1)
        self.clock+=timedelta(minutes=16);self.m.snapshot(f);self.assertEqual(f.call_count,2)
        bad=copy.deepcopy(self.s);bad['rows'].append(bad['rows'][0])
        with self.assertRaises(ValueError):MarketStore(self.store,force=True,now=lambda:self.clock).snapshot(lambda:bad)
        with self.store.connect() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM market_batches WHERE kind='snapshot'").fetchone()[0],2)
    def test_local_day_boundary_and_history_expiry(self):
        self.clock=datetime(2026,10,31,15,58,tzinfo=timezone.utc)
        f=Mock(return_value=self.s);self.m.snapshot(f)
        self.clock+=timedelta(minutes=5);self.m.snapshot(f)
        self.assertEqual(f.call_count,2)
        h=Mock(side_effect=hist);self.m.history(self.stock,self.s['as_of'],h)
        self.clock+=timedelta(hours=7);self.m.history(self.stock,self.s['as_of'],h)
        self.assertEqual(h.call_count,2)
    def test_expired_snapshot_failure_does_not_replace_saved_data(self):
        self.m.snapshot(lambda:self.s);old=self.m.cached('snapshot','universe')['id']
        self.clock+=timedelta(minutes=16)
        def broken():raise ValueError('公开来源离线')
        with self.assertRaises(ValueError):self.m.snapshot(broken)
        self.assertEqual(self.m.cached('snapshot','universe')['id'],old)
    def test_history_reuse_invalid_conflict_and_hash(self):
        f=Mock(side_effect=hist);h=self.m.history(self.stock,self.s['as_of'],f)
        self.m.history(self.stock,self.s['as_of'],f);self.assertEqual(f.call_count,1)
        with self.store.connect() as db:db.execute("UPDATE market_batches SET hash='corrupt' WHERE kind='history'")
        self.m.history(self.stock,self.s['as_of'],f);self.assertEqual(f.call_count,2)
        f.side_effect=lambda *a,**kw:copy.deepcopy(h)
        with self.assertRaises(ValueError):self.m.history(dict(self.stock,pct=self.stock['pct']+2),self.s['as_of'],f)
        self.assertEqual(self.m.stats['failed'],1);self.assertEqual(len(h['rows']),90)
    def test_incremental_merge_and_revision_full_refresh(self):
        old=hist(self.stock,'2026-10-29');old['basis']='test-stable';self.m.publish('history','1.600000','2026-10-29',old)
        def fetch(stock,day,start=None):
            if not start:raise AssertionError('Only delta expected')
            h=copy.deepcopy(old);h['rows']=h['rows'][-8:]+[dict(h['rows'][-1],date=day)];return h
        stock=dict(self.stock,pct=old['rows'][-1]['pct'],amount=old['rows'][-1]['amount'])
        h=self.m.history(stock,'2026-10-30',fetch)
        self.assertEqual(h['update_mode'],'incremental');self.assertEqual(len(h['rows']),90)
        calls=[]
        def revised(stock,day,start=None):calls.append(start);h=hist(stock,day);h['basis']='revised';return h
        self.m.history(self.stock,'2026-11-02',revised)
        self.assertEqual(len(calls),2);self.assertIsNone(calls[1]);self.assertEqual(self.m.stats['full_refresh'],1)
    def test_failed_delta_keeps_previous_cache(self):
        self.m.publish('history','1.600000','2026-10-29',hist(self.stock,'2026-10-29'))
        def broken(*a,**kw):raise ValueError('离线')
        with self.assertRaises(ValueError):self.m.history(self.stock,'2026-10-30',broken)
        self.assertIsNotNone(self.m.cached('history','1.600000','2026-10-29'))
        self.assertIsNone(self.m.cached('history','1.600000','2026-10-30'))
    def test_atomic_publish_and_duplicate_days(self):
        h=hist(self.stock,self.s['as_of']);h['rows'].append(h['rows'][-1])
        with self.assertRaises(ValueError):validate_history(h,self.s['as_of'])
        with self.assertRaises(Exception):self.m.publish('history','1.600000',self.s['as_of'],h)
        self.assertIsNone(self.m.cached('history','1.600000'))
    def test_retry_only_failed_and_parent_immutable(self):
        day=(datetime.now(CN)-timedelta(days=1)).date()
        # The historical fixture generates weekdays only; yesterday may be a weekend.
        while day.weekday()>=5:day-=timedelta(days=1)
        day=day.isoformat();s=fixtures(day);sc=ScreeningStore(self.store)
        def new(**kw):return sc.start(dict(template='volume',mode='live',request_id=str(uuid.uuid4()),**kw))[0]
        def flaky(stock,day,**kw):
            if stock['code']=='600000':raise ValueError('测试连接失败')
            return hist(stock,day)
        a=sc.execute(new(),snapshot_getter=lambda:s,history_getter=flaky)
        self.assertEqual(a['report']['counts']['history_failed'],1);original=digest(sc.get(a['id']));calls=[]
        def recover(stock,day,**kw):calls.append(stock['code']);return hist(stock,day)
        b=sc.execute(new(retry_of=a['id']),snapshot_getter=lambda:(_ for _ in ()).throw(AssertionError('fixed snapshot expected')),history_getter=recover)
        self.assertEqual(calls,['600000']);self.assertEqual(b['report']['counts']['history_failed'],0);self.assertEqual(original,digest(sc.get(a['id'])))
        c=sc.execute(new(),snapshot_getter=lambda:(_ for _ in ()).throw(AssertionError('cache expected')),history_getter=lambda *a,**k:(_ for _ in ()).throw(AssertionError('cache expected')))
        self.assertEqual(c['data_state']['stats']['history_reused'],9)
        self.assertEqual(c['report']['counts']['passed'],a['report']['counts']['passed']+1)
    def test_local_table_search_gaps_pagination_and_no_download(self):
        self.assertEqual(self.m.table()['total'],0)
        snapshot=copy.deepcopy(self.s)
        snapshot['rows'][0].update(amount=None,close=0,pct=-100)
        snapshot['rows'][1]['name']='ST fixture'
        self.m.publish('snapshot','universe',snapshot['as_of'],snapshot)
        self.m.publish('history','1.600002',snapshot['as_of'],hist(snapshot['rows'][2],snapshot['as_of']))
        first=self.m.table(limit=5,kind='snapshot');last=self.m.table(page=99,limit=5,kind='snapshot')
        self.assertEqual(first['total'],12);self.assertEqual(len(first['rows']),5)
        self.assertEqual(last['page'],3);self.assertEqual(len(last['rows']),2)
        self.assertEqual(self.m.table(state='missing')['rows'][0]['code'],'600000')
        self.assertIsNone(self.m.table(state='missing')['rows'][0]['pct'])
        self.assertEqual(self.m.table(state='excluded')['rows'][0]['code'],'600001')
        row=self.m.table(query='600002')['rows'][0]
        self.assertEqual(row['history_count'],90);self.assertEqual(row['history_date'],snapshot['as_of'])
        self.assertEqual(self.m.table(query='<script>')['total'],0)
        self.assertEqual(self.m.stats['snapshot_fetched'],0)
        for args in [dict(page=0),dict(limit=999),dict(state='bad'),dict(query='x'*81)]:
            with self.assertRaises(ValueError):self.m.table(**args)

    def test_all_dates_history_dedup_and_date_type_filters(self):
        older=fixtures('2026-10-29');self.m.publish('snapshot','universe',older['as_of'],older)
        self.m.publish('snapshot','universe',self.s['as_of'],self.s)
        stock=self.s['rows'][0];key='1.'+stock['code']
        old=hist(stock,'2026-10-29');current=hist(stock,self.s['as_of'])
        self.m.publish('history',key,'2026-10-29',old)
        self.m.publish('history',key,self.s['as_of'],current)
        revised=copy.deepcopy(current);revised['rows'][-1]['close']+=3
        self.m.publish('history',key,self.s['as_of'],revised)
        days={r['date'] for h in (old,current) for r in h['rows']}
        all_data=self.m.table(limit=100)
        self.assertEqual(all_data['total'],24+len(days))
        self.assertEqual(set(all_data['dates']),days)
        self.assertGreater(all_data['date_count'],2)
        self.assertEqual(self.m.table(kind='snapshot')['dates'],['2026-10-30','2026-10-29'])
        self.assertEqual(set(self.m.table(kind='history')['dates']),days)
        past=self.m.table(date=min(days),kind='history')
        self.assertEqual(past['total'],1);self.assertEqual(past['rows'][0]['date'],min(days))
        self.assertEqual(self.m.table(date='2026-10-29',kind='snapshot')['total'],12)
        latest=self.m.table(date=self.s['as_of'],kind='history')
        self.assertEqual(latest['total'],1);self.assertEqual(latest['rows'][0]['close'],revised['rows'][-1]['close'])
        self.assertNotEqual(latest['rows'][0]['basis'],'不复权（收盘快照）')
        self.assertEqual(self.m.table(date='2000-01-01')['total'],0)
        for args in [dict(date='2026-02-30'),dict(date='2026-1-1'),dict(kind='invalid')]:
            with self.assertRaises(ValueError):self.m.table(**args)

    def test_force_hash_and_interruption(self):
        sc=ScreeningStore(self.store);d=dict(template='volume',mode='live',request_id=str(uuid.uuid4()));a,_=sc.start(d)
        with self.assertRaises(ValueError):sc.duplicate(dict(d,force_refresh=True))
        sc.recover();self.assertEqual(sc.get(a['id'])['state'],'INTERRUPTED')
        with self.assertRaises(ValueError):sc.start(dict(d,request_id=str(uuid.uuid4()),force_refresh='yes'))

if __name__=='__main__':unittest.main()
