"""Teaching fixtures only. No fixture is used by the live strategy or live proof."""
import calendar
import copy
import json
import tempfile
import unittest
import uuid
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from functools import partial
from engine import Store
from market_store import MarketStore
from industry_data import TAXONOMY
from screening import strategy,ScreeningStore,FIVE_ID
from five_layer import run_five,month_range,outcome,rule
from five_sources import augment_ohlc,limit_pool,month_calendar,announcements

DAY='2026-10-30';CUTOFF='2026-10-31T12:00:00+08:00'

def fixture_market_clock():
    clock = lambda: datetime.fromisoformat(CUTOFF)
    return patch('screening.MarketStore', partial(MarketStore, now=clock))


def fixture():
    end=datetime.fromisoformat(DAY);days=[];d=end
    while len(days)<90:
        if d.weekday()<5:days.append(d.date().isoformat())
        d-=timedelta(days=1)
    days=sorted(days);snap=dict(as_of=DAY,data_kind='real',source='虚构单元测试夹具，非实测',scope='教学夹具',rows=[],warnings=[])
    registry={};actual={};histories={};groups={c:dict(name=name,members={}) for c,name in [('801010','教学甲'),('801030','教学乙')]}
    for i in range(20):
        code=str(600000+i);industry='教学甲' if i<10 else '教学乙';ic='801010' if i<10 else '801030'
        h=[]
        for j,day in enumerate(days):
            c=20+j*.01;h.append(dict(date=day,open=c-.005,close=c,high=c+.02,low=c-.02,amount=1.1e8,pct=.05))
        prev=h[-2]['close'];c=round(prev*1.1+1e-9,2) if i%10<2 else round(prev*1.08,2);pct=(c/prev-1)*100;amount=4e8-i*1e6
        h[-1].update(open=prev*1.01,close=c,high=c,low=prev*.999,amount=amount,pct=pct)
        stock=dict(code=code,name='教学'+str(i),market=1,industry=industry,industry_code=ic,date=DAY,quote_at=DAY+'T15:30:00+08:00',eod=True,close=c,pct=pct,amount=amount)
        snap['rows'].append(stock);histories[code]=dict(rows=h,basis='虚构测试前复权OHLC',evidence={})
        registry[code]=dict(code=code,name=stock['name'],listing_date='2000-01-01',market=1,board='主板')
        actual[code]=dict(code=code,date=DAY,at=DAY+'T15:30:00+08:00',close=c,previous=prev,open=prev*1.01,high=c,low=prev*.999,upper=round(prev*1.1+1e-9,2),lower=round(prev*.9+1e-9,2))
        groups[ic]['members'][code]=dict(name=stock['name'],begin='2000-01-01')
    classification=dict(taxonomy=TAXONOMY,observed_at=CUTOFF,observed_date='2026-10-31',catalog={k:v['name'] for k,v in groups.items()},groups=groups,failed_groups={},evidence={})
    resources={}
    for month in month_range(datetime.fromisoformat(DAY)-timedelta(days=380),datetime.fromisoformat(CUTOFF)):
        y,m=map(int,month.split('-'));rows=[dict(date=f'{month}-{i:02d}',is_open=datetime(y,m,i).weekday()<5) for i in range(1,calendar.monthrange(y,m)[1]+1)]
        resources['calendar:'+month]=lambda rows=rows:dict(rows=rows,evidence={'type':'synthetic unit fixture'})
    resources['registry:sse-szse']=lambda:dict(rows=registry)
    idx=[dict(date=day,open=100,close=100,high=101,low=99) for day in days]
    resources['index:000300']=lambda:dict(rows=idx,basis='test benchmark')
    for ic in groups:
        bars=[dict(date=day,open=100+j*.5,close=100+j*.5,high=101+j*.5,low=99+j*.5) for j,day in enumerate(days)]
        resources['sector_index:'+ic]=lambda bars=bars:dict(rows=bars,basis='test sector')
    resources['limit_prices:tencent']=lambda:dict(rows=actual,failures=[])
    resources['pool:previous']=lambda:dict(rows={str(600000+i):{} for i in (0,1,10,11)})
    for kind in ('up','down','broken'):resources['pool:'+kind]=lambda:dict(rows={})
    for code in histories:resources['announcements:'+code]=lambda:dict(rows=[],time_unknown=False,risk_hits=[],complete=True)
    return snap,classification,histories,resources

class FiveTests(unittest.TestCase):
    def run_fixture(self,snap,classification,histories,resources):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);store=Store(tmp.name);sc=ScreeningStore(store)
        item,_=sc.start(dict(template=FIVE_ID,mode='live',request_id=str(uuid.uuid4())))
        with fixture_market_clock(),patch('five_layer.timestamp',return_value=CUTOFF),patch('five_sources.public_bytes',side_effect=AssertionError('Fixture test must never access network')):
            result=sc.execute(item,snapshot_getter=lambda:snap,industry_getter=lambda:classification,history_getter=lambda s,d,**kw:histories[s['code']],five_resources=resources)
        return result,store
    def test_real_only_strategy_and_saved_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            sc=ScreeningStore(Store(tmp));saved=sc.save_strategy(dict(template=FIVE_ID,name='教学五层'))
            resolved=sc.resolve_strategy(dict(template=FIVE_ID,name=saved['name'],params=saved['params'],saved_id=saved['id']))
            self.assertEqual(resolved['saved_version'],1)
            with self.assertRaises(ValueError):sc.start(dict(template=FIVE_ID,mode='demo',request_id=str(uuid.uuid4())))
            with self.assertRaises(ValueError):strategy(dict(template=FIVE_ID,params={'min_pct':3}))
    def test_five_stages_full_fixture_and_ranking(self):
        r,store=self.run_fixture(*fixture())
        self.assertIn(r['state'],('COMPLETED','PARTIAL'));self.assertIn('report',r,r.get('message'))
        report=r['report'];self.assertEqual(report['counts']['passed'],4,report['summary'])
        self.assertTrue(all(l['status']=='通过' for l in report['layers']))
        self.assertEqual(report['five_layer']['price_as_of'],DAY)
        self.assertFalse(r['model_used']);self.assertFalse(report['five_layer']['required_data_missing'])
        self.assertTrue(all(e['research_rank_score']>=0 and e['research_rank_score']<=100 for e in report['evaluations'] if e['status']=='符合条件'))
        self.assertTrue(all(all(c['passed'] for c in e['checks']) for e in report['evaluations'] if e['status']=='符合条件'))
    def test_market_reject_does_not_fetch_stock_history(self):
        snap,cls,h,resources=fixture()
        for r in snap['rows']:r['pct']=-1
        result,_=self.run_fixture(snap,cls,{},resources)
        self.assertEqual(result['report']['counts']['checked'],0)
        self.assertEqual(result['report']['layers'][0]['status'],'不通过')
        self.assertTrue(all(l['status']=='未进入' for l in result['report']['layers'][1:]))
    def test_missing_previous_pool_never_becomes_empty_or_pass(self):
        snap,cls,h,res=fixture()
        def failed():raise ValueError('教学断连')
        res['pool:previous']=failed
        r,_=self.run_fixture(snap,cls,h,res)
        self.assertEqual(r['state'],'PARTIAL');self.assertEqual(r['report']['counts']['passed'],0)
        m4=next(x for x in r['report']['five_layer']['market_checks'] if x['id']=='M4')
        self.assertEqual(m4['status'],'待核验');self.assertIsNone(m4['value'])
    def test_missing_sector_index_blocks_ranking(self):
        s,c,h,res=fixture()
        def failed():raise ValueError('教学行业缺失')
        res['sector_index:801010']=failed
        r,_=self.run_fixture(s,c,h,res)
        self.assertEqual(r['state'],'PARTIAL');self.assertEqual(r['report']['counts']['passed'],0)
        self.assertTrue(all(next(v for v in x['checks'] if v['id']=='S1')['status']=='待核验' for x in r['report']['five_layer']['sector_evaluations']))
    def test_announcement_hit_excluded_from_passed(self):
        s,c,h,res=fixture();res['announcements:600000']=lambda:dict(rows=[],time_unknown=False,risk_hits=[dict(title='教学待核验')],complete=True)
        r,_=self.run_fixture(s,c,h,res)
        self.assertEqual(r['report']['counts']['passed'],3);self.assertEqual(r['state'],'PARTIAL')
        row=next(e for e in r['report']['evaluations'] if e['stock']['code']=='600000')
        self.assertEqual(row['status'],'待核验')
    def test_ohlc_no_raw_evidence_and_flat_range(self):
        s,c,h,res=fixture();h['600000']['rows'][-1]['high']=h['600000']['rows'][-1]['low']=h['600000']['rows'][-1]['close'];h['600000']['rows'][-1]['open']=h['600000']['rows'][-1]['close']
        r,_=self.run_fixture(s,c,h,res);self.assertEqual(r['report']['counts']['passed'],3)
        bad=copy.deepcopy(h['600001']);bad['rows'][0].pop('high')
        with self.assertRaises(ValueError):augment_ohlc(bad,s['rows'][1],DAY)
    def test_wrong_pool_date_and_calendar_coverage_are_rejected(self):
        with patch('five_sources.get',return_value=({'rc':0,'data':{'qdate':20261029,'tc':0,'pool':[]}},{})):
            with self.assertRaises(ValueError):limit_pool('up',DAY)
        with patch('five_sources.get',return_value=({'data':[]},{})):
            with self.assertRaises(ValueError):month_calendar('2026-10')
    def test_previous_pool_uses_its_supported_sort(self):
        payload={'rc':0,'data':{'qdate':20261030,'tc':1,'pool':[{'c':'600000','n':'教学','zdp':1}]}}
        with patch('five_sources.get',return_value=(payload,{})) as read:
            result=limit_pool('previous',DAY);self.assertEqual(len(result['rows']),1)
            self.assertEqual(read.call_args.args[1]['sort'],'zs:desc')

    def test_announcement_pagination_and_unknown_time(self):
        data={'data':dict(total_hits=1,list=[dict(art_code='fixture',codes=[dict(stock_code='600000')],title='未被立案（教学）',notice_date=DAY,display_time='')])}
        with patch('five_sources.get',return_value=(data,{})):
            r=announcements('600000','2026-10-26',CUTOFF);self.assertTrue(r['time_unknown']);self.assertEqual(len(r['risk_hits']),1)
        data['data']['total_hits']=2
        with patch('five_sources.get',return_value=(data,{})):
            with self.assertRaises(ValueError):announcements('600000','2026-10-26',CUTOFF)
    def test_announcement_query_uses_shanghai_calendar_date(self):
        data={'data':{'total_hits':0,'list':[]}}
        with patch('five_sources.get',return_value=(data,{})) as read:
            announcements('600000','2026-10-26','2026-10-30T17:00:00+00:00')
            self.assertEqual(read.call_args.args[1]['end_time'],'2026-10-31')

    def test_rule_missing_not_false_or_zero(self):
        r=rule('x','x',None,'≥',1);self.assertIsNone(r['passed']);self.assertEqual(outcome([r]),'待核验')
        self.assertEqual(outcome([rule('y','y',0,'≥',1)]),'不通过')

if __name__=='__main__':unittest.main()
