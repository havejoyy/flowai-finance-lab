import copy,json,tempfile,unittest,uuid
from datetime import datetime,timezone
from unittest.mock import Mock
from engine import Store
from industry_data import fetch_industries,attach_industries,parse_members,parse_catalog,validate_industries
from market_store import MarketStore
from test_market_store import fixtures,hist
from screening import analyze,strategy,ScreeningStore


def industry_fixture():
    s=fixtures();groups={'801010':{'name':'测试行业甲','members':{}},'801030':{'name':'测试行业乙','members':{}}}
    for i,r in enumerate(s['rows']):groups['801010' if i<8 else '801030']['members'][r['code']]={'name':r['name'],'begin':'2020-01-01'}
    return dict(taxonomy='申万一级行业（2021版指数体系，当前成分）',observed_at='2026-10-31T00:00:00+00:00',observed_date='2026-10-31',catalog={c:g['name'] for c,g in groups.items()},groups=groups,failed_groups={},evidence={})

class IndustryTests(unittest.TestCase):
    def test_fetch_catalog_pages_and_counts(self):
        def getter(path,p):
            rows=([dict(swindexcode='801010',swindexname='测试行业')] if path=='current/' else [dict(stockcode='600000',stockname='测试股票',beginningdate='2020-01-01T00:00:00+08:00')])
            return {'count':len(rows),'results':rows},{'url':'fixture','data':rows}
        d=fetch_industries(getter=getter);self.assertEqual(len(d['groups']),1);validate_industries(d)
        def bad(path,p):return {'count':2,'results':[]},{}
        with self.assertRaises(ValueError):fetch_industries(getter=bad)
    def test_duplicate_and_invalid_members(self):
        row=dict(stockcode='000001',stockname='测试',beginningdate='2020-01-01T00:00:00+08:00')
        self.assertEqual(set(parse_members([row])),{'000001'})
        for rows in [[row,row],[dict(row,stockcode=1)],[dict(row,beginningdate=None)]]:
            with self.assertRaises((ValueError,TypeError)):parse_members(rows)
        with self.assertRaises(ValueError):parse_catalog([dict(swindexcode='801010',swindexname='甲')]*2)
    def test_conflict_unknown_future_and_no_guess(self):
        s=fixtures();d=industry_fixture()
        d['groups']['801030']['members']['600000']=d['groups']['801010']['members']['600000']
        d['groups']['801010']['members']['600001']['begin']='2026-11-01'
        del d['groups']['801010']['members']['600002']
        r=attach_industries(s,d);self.assertEqual(r['industry_data']['matched'],9)
        self.assertEqual(len(r['industry_data']['conflicts']),1);self.assertEqual(len(r['industry_data']['unknown']),2)
        self.assertEqual(r['rows'][0]['industry'],'行业未知');self.assertEqual(s['rows'][0]['industry'],'示例行业甲')
    def test_sqlite_reuse_forced_refresh_failure_preserves_cache(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(root);m=MarketStore(store,now=lambda:datetime(2026,10,31,8,tzinfo=timezone.utc));f=Mock(return_value=industry_fixture())
            a=m.industry(fixtures(),f);b=m.industry(fixtures(),f);self.assertEqual(f.call_count,1)
            self.assertEqual(a['industry_data']['batch_id'],b['industry_data']['batch_id'])
            with store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM market_industry_members').fetchone()[0],12)
            def failed():raise ValueError('公开源离线')
            with self.assertRaises(ValueError):m.industry(fixtures(),failed,refresh=True)
            self.assertIsNotNone(m.cached('industry','sw-level1'));self.assertEqual(m.stats['industry_reused'],1)
    def test_missing_industry_is_pending_sector_not_failed_rule(self):
        d=industry_fixture();del d['groups']['801010']['members']['600000']
        s=attach_industries(fixtures(),d);r=analyze(s,strategy({'template':'sector'}),hist)
        self.assertEqual(r['counts']['industry_missing'],1);self.assertEqual(r['counts']['data_missing'],1)
        self.assertGreater(r['counts']['passed'],0);self.assertEqual(r['layers'][1]['status'],'统计已接入')
        for e in r['evaluations']:
            for c in e['checks']:self.assertTrue(c['passed'])
    def test_live_sector_auto_fetch_then_reuses_and_preserves_run(self):
        from engine import digest,CN
        from datetime import timedelta
        day=(datetime.now(CN)-timedelta(days=1)).date()
        # The historical fixture generates weekdays only; yesterday may be a weekend.
        while day.weekday()>=5:day-=timedelta(days=1)
        day=day.isoformat()
        with tempfile.TemporaryDirectory() as root:
            sc=ScreeningStore(Store(root))
            def new():return sc.start(dict(template='sector',mode='live',request_id=str(uuid.uuid4())))[0]
            f=Mock(return_value=industry_fixture())
            a=sc.execute(new(),snapshot_getter=lambda:fixtures(day),history_getter=hist,industry_getter=f)
            self.assertEqual(a['state'],'COMPLETED');self.assertEqual(f.call_count,1)
            self.assertEqual(a['report']['counts']['industry_missing'],0)
            original=digest(sc.get(a['id']))
            def offline(*a,**kw):raise AssertionError('must reuse validated cache')
            b=sc.execute(new(),snapshot_getter=offline,history_getter=offline,industry_getter=offline)
            self.assertEqual(b['state'],'COMPLETED');self.assertEqual(b['data_state']['stats']['industry_reused'],1)
            self.assertEqual(a['report']['counts'],b['report']['counts'])
            self.assertEqual(digest(sc.get(a['id'])),original)
    def test_partial_groups_not_silent_success(self):
        def getter(path,p):
            if path=='current/':rows=[dict(swindexcode='801010',swindexname='甲'),dict(swindexcode='801030',swindexname='乙')]
            else:
                if p['swindexcode']=='801030':raise ValueError('测试失败')
                rows=[dict(stockcode='600000',stockname='测试股票',beginningdate='2020-01-01')]
            return {'count':len(rows),'results':rows},{}
        r=fetch_industries(getter=getter);self.assertEqual(len(r['groups']),1);self.assertIn('801030',r['failed_groups'])
        r['failed_groups']={}
        with self.assertRaises(ValueError):validate_industries(r)

if __name__=='__main__':unittest.main()
