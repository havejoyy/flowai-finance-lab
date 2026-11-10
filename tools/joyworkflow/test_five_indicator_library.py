"""Five-layer selection and threshold integration; all fixtures explicitly synthetic."""
import copy,tempfile,unittest,uuid
from unittest.mock import patch
from engine import Store
from screening import ScreeningStore,strategy,FIVE_ID
from five_indicator_library import FIVE_INDICATORS,QUALITY_INDICATORS,defaults,validate_rules,RuleBook
from test_five_layer import fixture,CUTOFF,fixture_market_clock

class FiveLibraryTests(unittest.TestCase):
    def config(self,rows):return dict(template=FIVE_ID,five_rules=rows,mode='live',request_id=str(uuid.uuid4()))
    def run_rules(self,rows,resources_change=None):
        snap,classification,histories,resources=fixture()
        if resources_change:resources_change(resources)
        with tempfile.TemporaryDirectory() as tmp:
            sc=ScreeningStore(Store(tmp));item,_=sc.start(self.config(rows))
            with fixture_market_clock(),patch('five_layer.timestamp',return_value=CUTOFF),patch('five_sources.public_bytes',side_effect=AssertionError('never network')):
                result=sc.execute(item,snapshot_getter=lambda:snap,industry_getter=lambda:classification,history_getter=lambda s,d,**kw:histories[s['code']],five_resources=resources)
            self.assertIn('report',result,result['message']);return result
    def test_catalog_all_layers_and_legacy_default_identity(self):
        self.assertEqual(len(FIVE_INDICATORS),24);self.assertEqual({x['layer'] for x in FIVE_INDICATORS},{1,2,3,4,5})
        self.assertEqual(len(QUALITY_INDICATORS),10)
        old=strategy(dict(template=FIVE_ID));self.assertNotIn('five_rules',old)
        self.assertEqual(len(RuleBook(old).rows),24)
        for row in defaults(old['params']):self.assertEqual(validate_rules([row]),[row])
    def test_strict_validation_save_reload_and_hash(self):
        original=strategy(dict(template=FIVE_ID));rows=defaults(original['params'])
        for bad in [rows+rows[:1],[dict(id='G3',operator='>=',value=1)],[dict(id='M1',operator='>=',value=float('nan'))],[dict(id='S5',operator='>=',value=2.5)],[dict(id='R6',operator='=',value=0)],[dict(id='S1',operator='>=',value=20)]]:
            with self.assertRaises(ValueError):strategy(self.config(bad))
        with self.assertRaises(ValueError):strategy(self.config([]))
        with self.assertRaises(ValueError):strategy(dict(template='volume',five_rules=rows))
        changed=copy.deepcopy(rows);changed[0]['value']=65
        with tempfile.TemporaryDirectory() as tmp:
            sc=ScreeningStore(Store(tmp));one=sc.save_strategy(dict(self.config(rows),name='test five'));two=sc.save_strategy(dict(self.config(changed),name='test five'))
            self.assertEqual([one['saved_version'],two['saved_version']],[1,2]);self.assertNotEqual(one['config_hash'],two['config_hash'])
            resolved=sc.resolve_strategy(dict(self.config(changed),name='test five',saved_id=two['id']))
            self.assertEqual(resolved['five_rules'],two['five_rules']);self.assertEqual(resolved['rule_version'],'youzi-five-layer-v1.1.0')
            self.assertEqual(sc.strategies()[1]['five_rules'],one['five_rules'])
    def test_thresholds_control_each_layer_and_disabled_rules_do_not_apply(self):
        rows=defaults(strategy(dict(template=FIVE_ID))['params'])
        self.assertEqual(self.run_rules(rows)['report']['counts']['passed'],4)
        for key,value,op in [('M1',100,'>'),('S3',999,'>='),('C1',100,'>='),('P2',100,'>='),('R2',10000,'>=')]:
            custom=copy.deepcopy(rows);rule=next(x for x in custom if x['id']==key);rule.update(value=value,operator=op)
            result=self.run_rules(custom);self.assertEqual(result['report']['counts']['passed'],0,key)
            # Removing this exact condition must restore base membership, not retain a hidden template default.
            without=[x for x in custom if x['id']!=key]
            result=self.run_rules(without);self.assertEqual(result['report']['counts']['passed'],4,key)
            self.assertNotIn(key,{x['id'] for x in result['report']['five_layer']['selected_rules']})
    def test_selected_sparse_strategy_keeps_data_guards_and_skips_notice(self):
        rows=[dict(id='C1',operator='>=',value=2)]
        def fail_notices(resources):
            for key in list(resources):
                if key.startswith('announcements:'):resources[key]=lambda:(_ for _ in ()).throw(AssertionError('unselected announcement not requested'))
            resources['pool:previous']=lambda:(_ for _ in ()).throw(ValueError('optional previous pool absent'))
        result=self.run_rules(rows,fail_notices);report=result['report']
        self.assertGreater(report['counts']['passed'],0)
        self.assertFalse(report['five_layer']['required_data_missing'])
        self.assertEqual(report['five_layer']['stages'][0]['status'],'未设置筛选条件')
        self.assertTrue(any(c['id']=='G3' and c['passed'] for c in report['five_layer']['market_checks']))
        self.assertFalse(any(s['dataset'].startswith('announcements:') for s in report['five_layer']['sources']))
    def test_missing_data_guard_cannot_be_bypassed_by_deselecting_rules(self):
        rows=[dict(id='C1',operator='>=',value=2)]
        def missing(resources):resources['limit_prices:tencent']=lambda:dict(rows={},failures=['missing'])
        result=self.run_rules(rows,missing);self.assertEqual(result['report']['counts']['passed'],0)
        self.assertTrue(result['report']['five_layer']['required_data_missing'])

if __name__=='__main__':unittest.main()
