import copy
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from engine import Store, csv_rows
from providers import ai_review, sync_market
from export_microapp import candidate_code


def payload():
    return {'plan': {'name': 'test', 'version': 'v1', 'definition': 'explicit rule',
                    'horizons': [1, 5], 'source': 'csv', 'benchmark': '000300.SH'},
            'as_of': '2026-01-05', 'candidates': [{'code': '000001.SZ'}]}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def market(self, factor=1, second=11):
        rows = [{'code': c, 'date': d, 'close': v, 'factor': f} for c,d,v,f in [
            ('000001.SZ','2026-01-05',10,1),('000001.SZ','2026-01-06',second,factor),
            ('000300.SH','2026-01-05',100,1),('000300.SH','2026-01-06',101,1)]]
        cal = [{'date':'2026-01-05','is_open':1}, {'date':'2026-01-06','is_open':1}]
        self.store.import_market('csv',rows,cal)

    def test_demo_and_dedup(self):
        a = self.store.demo(); self.store.demo()
        self.assertEqual(len(self.store.batches()),1)
        self.assertEqual(len(a['results']),4)
        self.assertTrue(all(r['status']=='MATURE' for r in a['results']))
        self.assertEqual(len(self.store.state()['evaluations']),2)

    def test_rule_version_immutable(self):
        p = payload(); self.store.create_batch(p)
        p['plan']['definition']='changed'
        with self.assertRaises(ValueError): self.store.create_batch(p)
        p['plan']['version']='v2'; self.store.create_batch(p)
        self.assertEqual(len(self.store.batches()),2)

    def test_identical_dedup_changed_candidates_retained(self):
        p=payload(); a=self.store.create_batch(p)
        self.assertEqual(self.store.create_batch(p)['id'],a['id'])
        p['candidates'][0]['reason']='new info'; self.store.create_batch(p)
        self.assertEqual(len(self.store.batches()),2)

    def test_adjustment_and_immature(self):
        self.market(2,5.5)
        r=self.store.evaluate(self.store.create_batch(payload())['id'],'2026-01-06')
        self.assertAlmostEqual(r['results'][0]['observation_return'],.1)
        self.assertAlmostEqual(r['results'][0]['excess_return'],.09)
        self.assertEqual(r['results'][1]['status'],'IMMATURE')

    def test_calendar_missing(self):
        r=self.store.evaluate(self.store.create_batch(payload())['id'],'2026-01-06')
        self.assertEqual(r['results'][0]['status'],'MISSING_CALENDAR')

    def test_price_missing(self):
        self.market()
        p=payload(); p['candidates'][0]['code']='600000.SH'
        r=self.store.evaluate(self.store.create_batch(p)['id'],'2026-01-06')
        self.assertEqual(r['results'][0]['status'],'MISSING_PRICE')
        self.assertIsNone(r['summary'][0]['mean_observation_return'])

    def test_revision_preserves_evidence(self):
        self.market(); identifier=self.store.create_batch(payload())['id']
        first=self.store.evaluate(identifier,'2026-01-06')
        self.market(second=12); second=self.store.evaluate(identifier,'2026-01-06')
        self.assertNotEqual(first['results'][0]['observation_return'],second['results'][0]['observation_return'])
        self.assertEqual(len(self.store.state()['evaluations']),2)

    def test_validation(self):
        for edit in [lambda p:p.update(as_of='2999-01-01'),
                     lambda p:p['plan'].update(horizons=[1.5]),
                     lambda p:p['candidates'].append({'code':'000001.SZ'}),
                     lambda p:p['candidates'][0].update(code='000300.SH')]:
            p=payload(); edit(p)
            with self.assertRaises(ValueError): self.store.create_batch(p)
        with self.assertRaises(ValueError):
            self.store.import_market('csv',[{'code':'000001.SZ','date':'2026-01-05','close':'NaN','factor':1}],[])

    def test_all_batches_partial_sync(self):
        self.market(); p=payload(); self.store.create_batch(p)
        p['candidates'][0]['reason']='second'; self.store.create_batch(p)
        def fail(*args): raise RuntimeError('offline')
        result=self.store.run('2026-01-06',fail)
        self.assertEqual(result['status'],'PARTIAL')
        self.assertEqual(len(result['evaluations']),2)

    def test_demo_excluded_from_daily(self):
        self.store.demo()
        self.assertEqual(self.store.run('2026-01-06')['evaluations'],[])

    def test_empty_batch(self):
        p=payload(); p['candidates']=[]
        r=self.store.evaluate(self.store.create_batch(p)['id'],'2026-01-06')
        self.assertEqual(r['results'],[])
        self.assertEqual(r['summary'][0]['mature'],0)

    def test_backup_and_recovery(self):
        self.store.demo()
        self.assertTrue(Path(self.store.backup()).exists())
        with self.store.connect() as db:
            db.execute("INSERT INTO runs VALUES('x','RUNNING','date',NULL,'{}')")
        self.store.recover()
        self.assertEqual(self.store.state()['runs'][0]['status'],'INTERRUPTED')

    def test_csv(self):
        self.assertEqual(csv_rows('\ufeffcode,reason\n000001.SZ,"a,b"\n')[0]['reason'],'a,b')

    def test_microapp_codes(self):
        self.assertEqual(candidate_code('600519'),'600519.SH')
        self.assertEqual(candidate_code('000001'),'000001.SZ')
        self.assertEqual(candidate_code('920001'),'920001.BJ')
        with self.assertRaises(ValueError): candidate_code('123456')

    def test_ai_payload_and_url_restriction(self):
        r=self.store.demo()
        with patch('providers.post_json',return_value={'choices':[{'message':{'content':'test'}}]}) as call:
            self.assertEqual(ai_review(r,{'llm_url':'https://example.com/v1/chat/completions','llm_model':'test'}),'test')
            sent=json.loads(call.call_args[0][1]['messages'][1]['content'])
            self.assertNotIn('evidence',sent['results'][0])
        with self.assertRaises(ValueError): ai_review(r,{'llm_url':'http://example.com/v1/chat/completions','llm_model':'test'})

    def test_tushare_adapter_offline(self):
        p=payload(); p['plan']['source']='tushare'; self.store.create_batch(p)
        def fake(api,token,params,fields):
            if api=='trade_cal':return [{'cal_date':'20260105','is_open':1},{'cal_date':'20260106','is_open':1}]
            if api=='adj_factor':return [{'trade_date':'20260105','adj_factor':1},{'trade_date':'20260106','adj_factor':1}]
            return [{'trade_date':'20260105','close':10},{'trade_date':'20260106','close':11}]
        with patch('providers.tushare',side_effect=fake):
            result=sync_market(self.store,self.store.batches(),'2026-01-06','fake')
        self.assertEqual(result['prices'],4)
        self.assertEqual(self.store.run('2026-01-06')['status'],'COMPLETED')


class HTTPTests(unittest.TestCase):
    def test_http_security_and_demo(self):
        with tempfile.TemporaryDirectory() as root:
            process=subprocess.Popen([sys.executable,'app.py','--no-browser','--port','0','--data-dir',root],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
            try:
                line=process.stdout.readline().strip()
                url=line.removeprefix('JoyWorkflow: ')
                self.assertTrue(url.startswith('http://127.0.0.1:'))
                with urlopen(url+'/observe') as r: html=r.read().decode()
                token=html.split("const token='")[1].split("'")[0]
                for headers in ({},{'X-Workflow-Token':token,'Origin':'https://evil.example'}):
                    with self.assertRaises(HTTPError) as ctx:
                        urlopen(Request(url+'/api/demo',data=b'{}',headers=headers))
                    self.assertEqual(ctx.exception.code,403)
                with urlopen(Request(url+'/api/demo',data=b'{}',headers={'X-Workflow-Token':token})) as r:
                    self.assertEqual(len(json.load(r)['results']),4)
                with urlopen(url+'/api/state') as r:
                    self.assertEqual(len(json.load(r)['batches']),1)
                with self.assertRaises(HTTPError):
                    urlopen(Request(url+'/api/state',headers={'Host':'evil.example'}))
            finally:
                process.terminate(); process.communicate(timeout=5)


if __name__=='__main__':
    unittest.main()
