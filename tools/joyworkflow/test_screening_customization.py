"""Optional conditions and duplicate lookup; fixtures never replace live data."""
import tempfile,unittest,uuid
from unittest.mock import patch
from engine import Store
from screening import ScreeningStore,strategy,analyze,FIVE_ID
from screening_data import demo_snapshot,demo_history
from screening_indicators import indicator_checks
from test_five_layer import fixture,CUTOFF,fixture_market_clock

class CustomizationTests(unittest.TestCase):
    def test_validation_hash_and_saved_roundtrip(self):
        base=strategy({'template':'volume'})
        self.assertEqual(base,strategy({'template':'volume','indicators':[]}))
        extra=[dict(id='deviation20',operator='<=',value=12)]
        configured=strategy({'template':'volume','indicators':extra})
        self.assertNotEqual(base['config_hash'],configured['config_hash'])
        for bad in [[dict(id='eval(code)',operator='>=',value=0)],extra*2,[dict(id='return20',operator='!=',value=1)],[dict(id='return20',operator='>=',value=True)],[dict(id='return20',operator='>=',value=None)]]:
            with self.assertRaises(ValueError):strategy({'template':'volume','indicators':bad})
        with tempfile.TemporaryDirectory() as tmp:
            sc=ScreeningStore(Store(tmp));saved=sc.save_strategy({'template':'volume','indicators':extra})
            self.assertEqual(sc.resolve_strategy(dict(template='volume',indicators=extra,saved_id=saved['id']))['saved_version'],1)
            with self.assertRaises(ValueError):sc.resolve_strategy(dict(template='volume',saved_id=saved['id']))
    def test_optional_rule_changes_membership_and_exposes_values(self):
        snap=demo_snapshot();base=analyze(snap,strategy({'template':'volume'}),demo_history)
        self.assertGreater(base['counts']['passed'],0)
        config=strategy({'template':'volume','indicators':[dict(id='return20',operator='>=',value=1000)]})
        result=analyze(snap,config,demo_history)
        self.assertEqual(result['counts']['passed'],0)
        for ev in result['evaluations']:
            if ev['metrics']:
                check=ev['checks'][-1];self.assertEqual(check['id'],'CUSTOM_return20');self.assertFalse(check['passed']);self.assertEqual(check['target'],1000)
        h=demo_history(snap['rows'][0],snap['as_of'])
        config=strategy({'template':'volume','indicators':[dict(id='average20_amount',operator='>=',value=1),dict(id='deviation20',operator='<=',value=12)]})
        checks={r['id']:r for r in indicator_checks(config,h)}
        self.assertAlmostEqual(checks['CUSTOM_average20_amount']['value'],sum(r['amount'] for r in h['rows'][-20:])/20/1e8)
    def test_five_layer_optional_rule_enforced(self):
        snap,classification,histories,resources=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            sc=ScreeningStore(Store(tmp));item,_=sc.start(dict(template=FIVE_ID,mode='live',request_id=str(uuid.uuid4()),indicators=[dict(id='return20',operator='>=',value=1000)]))
            with fixture_market_clock(),patch('five_layer.timestamp',return_value=CUTOFF),patch('five_sources.public_bytes',side_effect=AssertionError('no network')):
                result=sc.execute(item,snapshot_getter=lambda:snap,industry_getter=lambda:classification,history_getter=lambda s,d,**kw:histories[s['code']],five_resources=resources)
            self.assertEqual(result['report']['counts']['passed'],0)
            self.assertTrue(any(c['id']=='CUSTOM_return20' for ev in result['report']['evaluations'] for c in ev['checks']))
    def test_preflight_readonly_scope_and_no_duplicate_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            sc=ScreeningStore(Store(tmp));request=dict(template='volume',mode='demo',request_id=str(uuid.uuid4()))
            self.assertIsNone(sc.previous_screening(request))
            item,_=sc.start(request)
            self.assertEqual(sc.previous_screening(request)['state'],'RUNNING')
            result=sc.execute(item)
            before=len(sc.list());found=sc.previous_screening(dict(request,request_id=str(uuid.uuid4())))
            self.assertEqual(found['id'],result['id']);self.assertEqual(found['as_of'],result['report']['as_of'])
            self.assertEqual(len(sc.list()),before)
            self.assertIsNone(sc.previous_screening(dict(request,mode='live')))
            self.assertIsNone(sc.previous_screening(dict(request,indicators=[dict(id='return20',operator='>=',value=1)])))
            # Duplicate detection also covers records outside the latest 50 display limit.
            for i in range(51):
                other,_=sc.start(dict(request,name='other'+str(i),request_id=str(uuid.uuid4())))
            self.assertEqual(sc.previous_screening(request)['id'],result['id'])

if __name__=='__main__':unittest.main()
