import subprocess,sys,tempfile,time,unittest,uuid,json
from datetime import datetime
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from unittest.mock import patch, MagicMock
from urllib.request import ProxyHandler
from http.client import RemoteDisconnected
from engine import Store,CN,encode
from screening import ScreeningStore,strategy,metrics,analyze
from screening_data import (fetch_snapshot,parse_history,demo_snapshot,demo_history,number,
                           public_bytes,SourceUnavailable,parse_sina_quotes,sina_catalog,validate_snapshot_dates,
                           parse_secondary_history,fetch_history,fetch_secondary_history,parse_qfq_factors)

def request(template='volume',**kw):
    return dict(template=template,mode='demo',request_id=str(uuid.uuid4()),**kw)

class ScreeningTests(unittest.TestCase):
    def test_public_transport_ignores_ambient_proxy_only_for_quotes(self):
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{}'
        opener=MagicMock();opener.open.return_value=response
        with patch.dict('os.environ',{},clear=True),patch('screening_data.build_opener',return_value=opener) as build:
            self.assertEqual(public_bytes('https://hq.sinajs.cn/list=sh600000','https://finance.sina.com.cn/'),b'{}')
            proxies=[h for h in build.call_args.args if isinstance(h,ProxyHandler)]
            self.assertEqual(proxies[0].proxies,{})
        with patch.dict('os.environ',{'JOYWORKFLOW_MARKET_PROXY':'environment'}),patch('screening_data.build_opener',return_value=opener) as build:
            public_bytes('https://hq.sinajs.cn/list=sh600000','https://finance.sina.com.cn/')
            self.assertFalse(any(isinstance(h,ProxyHandler) for h in build.call_args.args))
        opener.open.side_effect=RemoteDisconnected('closed')
        with patch('screening_data.build_opener',return_value=opener),patch('screening_data.time.sleep'):
            before=opener.open.call_count
            with self.assertRaises(SourceUnavailable):public_bytes('https://hq.sinajs.cn/','https://finance.sina.com.cn/')
            self.assertEqual(opener.open.call_count-before,2)
    def test_live_fallback_only_on_transport_or_refused_redirect(self):
        def failed(u,p):raise SourceUnavailable('断连')
        with patch('screening_data.fetch_sina_snapshot',return_value={'data_kind':'real'}) as fallback:
            self.assertEqual(fetch_snapshot(getter=failed)['fallback_reason'],'断连')
            fallback.assert_called_once()
        with patch('screening_data.fetch_sina_snapshot') as fallback:
            with self.assertRaises(ValueError):fetch_snapshot(getter=lambda u,p:{'total':1,'diff':[]})
            fallback.assert_not_called()
        for code in (302,307,403):
            error=HTTPError('https://push2.eastmoney.com/api/',code,'status',{'Location':'https://untrusted.example/'},None)
            def failed(u,p):raise error
            with patch('screening_data.fetch_sina_snapshot',return_value={'data_kind':'real'}) as fallback:
                if code==403:
                    with self.assertRaises(HTTPError):fetch_snapshot(getter=failed)
                    fallback.assert_not_called()
                else:
                    self.assertIn(str(code),fetch_snapshot(getter=failed)['fallback_reason']);fallback.assert_called_once()

    def test_sina_units_dates_coverage_and_missing(self):
        parts=['0']*33
        parts[0]='测试';parts[2]='10';parts[3]='11';parts[9]='123456789.12'
        parts[30]='2026-10-30';parts[31]='15:34:59'
        raw='var hq_str_sh600000="'+','.join(parts)+'";'
        catalog={'sh600000':'测试'}
        rows=parse_sina_quotes(raw,catalog)
        self.assertAlmostEqual(rows[0]['pct'],10);self.assertEqual(rows[0]['amount'],123456789.12)
        self.assertEqual(rows[0]['industry'],'行业未知')
        now=datetime(2026,10,30,20,tzinfo=CN)
        self.assertEqual(validate_snapshot_dates(rows,now),'2026-10-30');self.assertTrue(rows[0]['eod'])
        for bad in ['',raw+raw,raw.replace('600000','600001'),raw+'alert(1)']:
            with self.assertRaises(ValueError):parse_sina_quotes(bad,catalog)
        empty=parse_sina_quotes('var hq_str_sh600000="";',catalog)
        self.assertIsNone(empty[0]['amount'])
        with self.assertRaises(ValueError):validate_snapshot_dates(rows,datetime(2026,10,29,20,tzinfo=CN))
    def test_sina_directory_validation(self):
        def read(url):
            if 'StockCount' in url:return '"3"'
            return json.dumps([{'symbol':'sh600000','name':'甲'},{'symbol':'sz000001','name':'乙'},{'symbol':'bj920001','name':'丙'}])
        self.assertEqual(set(sina_catalog(read)),{'sh600000','sz000001'})
        def duplicate(url):return '"2"' if 'StockCount' in url else '[{"symbol":"sh600000"},{"symbol":"sh600000"}]'
        with self.assertRaises(ValueError):sina_catalog(duplicate)
        def short(url):return '"2"' if 'StockCount' in url else '[{"symbol":"sh600000"}]'
        with self.assertRaises(ValueError):sina_catalog(short)
    def test_missing_industry_is_not_rule_rejection(self):
        s=demo_snapshot()
        for row in s['rows']:row['industry']='行业未知'
        r=analyze(s,strategy({'template':'sector'}),demo_history)
        self.assertEqual(r['counts']['data_missing'],12)
        self.assertEqual(r['layers'][3]['status'],'待核验')
        self.assertEqual(r['counts']['rejected'],0)
        r=analyze(s,strategy({'template':'volume'}),demo_history)
        self.assertGreater(r['counts']['passed'],0)
    def test_secondary_history_alignment_units_and_mismatch(self):
        import copy
        s=demo_snapshot();history=demo_history(s['rows'][0],s['as_of'])['rows']
        sohu=[{'status':0,'name':'测试证券','hq':[[r['date'],'10',str(r['close']),'0',str(r['pct'])+'%','9','11','100','12345.67'] for r in reversed(history)]}]
        factors_raw='var sh600000qfq='+json.dumps({'total':2,'data':[{'d':'2026-01-15','f':'1'},{'d':'1900-01-01','f':'2'}]})+'; /* source metadata */'
        factors=parse_qfq_factors(factors_raw,'sh600000',s['as_of'])
        rows=parse_secondary_history(sohu,factors,s['as_of'])
        self.assertEqual(len(rows),90);self.assertEqual(rows[-1]['amount'],123456700)
        self.assertEqual(rows[0]['close'],history[0]['close']/2)
        self.assertEqual(rows[-1]['close'],history[-1]['close'])
        for raw in [factors_raw.replace('sh600000','sh600001'),factors_raw.replace('2026-01-15','2026-02-15'),factors_raw.replace('"2"','"0"')]:
            with self.assertRaises(ValueError):parse_qfq_factors(raw,'sh600000',s['as_of'])
        short=copy.deepcopy(sohu);short[0]['hq']=short[0]['hq'][:30]
        stale=copy.deepcopy(sohu);stale[0]['hq'].pop(0)
        duplicate=copy.deepcopy(sohu);duplicate[0]['hq'].append(duplicate[0]['hq'][-1])
        for bad in [short,stale,duplicate]:
            with self.assertRaises(ValueError):parse_secondary_history(bad,factors,s['as_of'])
        with self.assertRaises(ValueError):parse_secondary_history(sohu,[('2026-01-15',1)],s['as_of'])
        def remote(url,referer):
            return (factors_raw if url.endswith('qfq.js') else json.dumps(sohu,ensure_ascii=False)).encode('gb18030')
        with patch('screening_data.public_bytes',side_effect=remote):
            self.assertEqual(fetch_secondary_history({'code':'600000','market':1},s['as_of'])['rows'][-1]['date'],s['as_of'])
        with patch('screening_data.fetch_secondary_history',return_value={'rows':rows}) as fallback:
            def failed(u,p):raise SourceUnavailable('断连')
            self.assertEqual(fetch_history({'code':'600000','market':1},s['as_of'],getter=failed)['fallback_reason'],'断连')
            fallback.assert_called_once()
        with patch('screening_data.fetch_secondary_history',return_value={'rows':rows}) as fallback:
            def redirected(u,p):raise HTTPError('https://push2his.eastmoney.com',302,'redirect',{},None)
            self.assertIn('302',fetch_history({'code':'600000','market':1},s['as_of'],getter=redirected)['fallback_reason']);fallback.assert_called_once()

    def test_parameter_boundaries(self):
        for params in [{'max_check':10.5},{'max_check':121},{'volume_ratio':float('nan')},{'min_pct':True},{'extra':1},[]]:
            with self.assertRaises(ValueError):strategy(dict(template='volume',params=params))
        self.assertEqual(strategy(dict(template='volume'))['params']['volume_ratio'],2)
        self.assertIsNone(number('nan'));self.assertIsNone(number(True))
    def test_prior_five_excludes_current(self):
        s=demo_snapshot();h=demo_history(s['rows'][0],s['as_of']);h['rows'][-1]['amount']=1000
        for row in h['rows'][-6:-1]:row['amount']=100
        m=metrics(h);self.assertEqual(m['average5'],100);self.assertEqual(m['volume_ratio'],10)
        self.assertAlmostEqual(m['return20'],(h['rows'][-1]['close']/h['rows'][-21]['close']-1)*100)
    def test_three_templates_no_relaxation(self):
        s=demo_snapshot();reports={k:analyze(s,strategy(dict(template=k)),demo_history) for k in ['volume','reversal','sector']}
        self.assertGreater(reports['volume']['counts']['passed'],reports['reversal']['counts']['passed'])
        self.assertGreater(reports['sector']['counts']['passed'],0)
        self.assertTrue(all(e['stock']['industry']=='示例行业甲' for e in reports['sector']['evaluations']))
        r=analyze(s,strategy(dict(template='volume',params={'volume_ratio':10})),demo_history)
        self.assertEqual(r['counts']['passed'],0);self.assertEqual(r['layers'][3]['status'],'已核验')
        self.assertEqual(len(r['layers']),5);self.assertEqual(r['layers'][1]['status'],'未接入')
    def test_coverage_history_failures(self):
        s=demo_snapshot();c=strategy(dict(template='volume',params={'min_pct':-5,'max_check':10}))
        def history(stock,day):
            if stock['code']=='DEMO00':raise ValueError('network unavailable')
            return demo_history(stock,day)
        r=analyze(s,c,history)
        self.assertEqual(r['counts']['deferred'],2);self.assertEqual(r['counts']['history_failed'],1)
        self.assertEqual(len(r['evaluations']),10);self.assertEqual(r['layers'][3]['status'],'已核验')
        self.assertEqual(r['counts']['checked'],r['counts']['passed']+r['counts']['history_failed']+r['counts']['rejected'])
    def test_missing_stale_conflicting(self):
        s=demo_snapshot();s['rows'][0]['eod']=False;s['rows'][1]['name']='*ST示例'
        def history(stock,day):
            h=demo_history(stock,day);h['rows'][-1]['pct']+=1;return h
        r=analyze(s,strategy(dict(template='volume')),history)
        self.assertEqual(r['counts']['excluded'],1);self.assertEqual(r['counts']['data_missing'],1)
        self.assertEqual(r['counts']['passed'],0);self.assertEqual(r['counts']['history_failed'],r['counts']['checked'])
        self.assertEqual(r['market']['count'],10)
    def test_source_pagination_close_time(self):
        now=datetime(2026,10,29,20,tzinfo=CN)
        def row(code,at=15):return dict(f12=code,f13=1,f14='测试',f2=10,f3=1,f6=1e8,f100='行业甲',f124=datetime(2026,10,29,at,tzinfo=CN).timestamp())
        pages=[dict(total=2,diff=[row('600001')]),dict(total=2,diff=[row('600002',14)])]
        snap=fetch_snapshot(getter=lambda u,p:pages[p['pn']-1],now=now)
        self.assertEqual(len(snap['rows']),2);self.assertFalse(snap['rows'][1]['eod'])
        with self.assertRaises(ValueError):fetch_snapshot(getter=lambda u,p:pages[0],now=now)
        with self.assertRaises(ValueError):fetch_snapshot(getter=lambda u,p:dict(total=1,diff=[row('600001',14)]),now=datetime(2026,10,29,14,30,tzinfo=CN))
        with self.assertRaises(ValueError):fetch_snapshot(getter=lambda u,p:dict(total=1,diff=[row('600001')]),now=datetime(2026,11,10,20,tzinfo=CN))
    def test_history_validation(self):
        snap=demo_snapshot();h=demo_history(snap['rows'][0],snap['as_of'])
        lines=[','.join([r['date'],'10',str(r['close']),'11','9','100',str(r['amount']),'0',str(r['pct']),'0','1']) for r in h['rows']]
        good=dict(code='600000',klines=lines)
        self.assertEqual(len(parse_history(good,'600000',snap['as_of'])),90)
        badlines=list(lines);parts=badlines[0].split(',');parts[6]='NaN';badlines[0]=','.join(parts)
        for bad in [dict(good,code='600001'),dict(good,klines=lines[:-1]),dict(good,klines=lines+[lines[-1]]),dict(good,klines=lines[-30:]),dict(good,klines=badlines)]:
            with self.assertRaises(ValueError):parse_history(bad,'600000',snap['as_of'])
    def test_versions_idempotency_recovery_failure(self):
        with tempfile.TemporaryDirectory() as root:
            store=ScreeningStore(Store(root));s1=store.save_strategy(dict(template='volume',name='我的策略'));s2=store.save_strategy(dict(template='volume',name='我的策略',params={'volume_ratio':3}))
            self.assertEqual(s1['saved_version'],1);self.assertEqual(s2['saved_version'],2)
            data=request(name='我的策略',saved_id=s1['id']);item,started=store.start(data)
            self.assertTrue(started);self.assertFalse(store.start(data)[1]);self.assertEqual(item['strategy']['saved_version'],1)
            with self.assertRaises(ValueError):store.start(dict(data,params={'volume_ratio':4}))
            r=store.execute(item);self.assertEqual(r['state'],'COMPLETED');self.assertFalse(r['model_used']);self.assertIn('documents',r['skill'])
            self.assertIn('histories',r['report']);self.assertIn('evidence',r['snapshot'])
            interrupted,_=store.start(request());store.recover();self.assertEqual(store.get(interrupted['id'])['state'],'INTERRUPTED')
            failing,_=store.start(request());r=store.execute(failing,snapshot_getter=lambda:(_ for _ in ()).throw(ValueError('取数失败')))
            self.assertEqual(r['state'],'FAILED');self.assertNotIn('report',r)

class ScreeningHTTPTests(unittest.TestCase):
    def test_page_strategy_demo_export_restart(self):
        with tempfile.TemporaryDirectory() as root:
            process=None
            def launch():
                proc=subprocess.Popen([sys.executable,'app.py','--no-browser','--port','0','--data-dir',root],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
                line=proc.stdout.readline();self.assertTrue(line.startswith('JoyWorkflow:'),line)
                address=line.split('JoyWorkflow: ')[1].strip()
                with urlopen(address) as r:html=r.read().decode()
                self.assertIn('获取数据并运行筛选',html)
                self.assertIn('id="run"',html)
                return proc,address,html.split("token='")[1].split("'")[0]
            def call(path,data=None,origin=None):
                headers={'Content-Type':'application/json','X-Workflow-Token':token}
                if origin:headers['Origin']=origin
                with urlopen(Request(url+'/api/'+path,data=encode(data).encode() if data is not None else None,headers=headers),timeout=5) as r:return json.load(r)
            try:
                process,url,token=launch();self.assertEqual(len(call('screen-presets')['presets']),4)
                self.assertEqual(call('screen-data-table')['total'],0)
                self.assertEqual(call('screen-data-table')['dates'],[])
                with self.assertRaises(HTTPError):call('screen-data-table?date=invalid')
                with self.assertRaises(HTTPError):call('screen-data-table?page=-1')
                custom=call('screen-strategies',dict(template='sector',name='自定义共振'))
                payload=request('sector',name=custom['name'],saved_id=custom['id'])
                with self.assertRaises(HTTPError):call('screen-runs',payload,origin='https://external.example')
                item=call('screen-runs',payload);duplicate=call('screen-runs',payload)
                self.assertEqual(item['id'],duplicate['id']);self.assertTrue(duplicate['deduplicated'])
                for _ in range(60):
                    result=call('screen-runs?id='+item['id'])
                    if result['state']!='RUNNING':break
                    time.sleep(.05)
                self.assertEqual(result['state'],'COMPLETED');self.assertNotIn('rows',result['snapshot']);self.assertNotIn('histories',result['report'])
                export=call('screen-export?id='+item['id']);self.assertIn('rows',export['snapshot']);self.assertIn('histories',export['report'])
                self.assertEqual(result['report']['data_scope'],export['report']['data_scope'])
                self.assertEqual(result['report']['data_scope']['source'],result['report']['counts']['source'])
                self.assertTrue(any('虚构示例' in n for n in result['report']['data_scope']['notes']))
                self.assertEqual(export['strategy']['saved_version'],1)
                with self.assertRaises(HTTPError):call('screen-runs',request(params={'max_check':999}))
                process.terminate();process.communicate(timeout=5);process,url,token=launch()
                self.assertEqual(call('screen-runs?id='+item['id'])['state'],'COMPLETED');self.assertEqual(len(call('screen-presets')['saved']),1)
                self.assertEqual(call('screen-runs?id='+item['id'])['report']['data_scope'],export['report']['data_scope'])
            finally:
                if process:process.terminate();process.communicate(timeout=5)

if __name__=='__main__':unittest.main()
