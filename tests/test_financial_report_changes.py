"""Behavioral tests using synthetic data, not investment examples."""
import copy
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).parents[1] / 'skills/financial-report-changes/scripts/compare.py'
spec = importlib.util.spec_from_file_location('changes', SCRIPT)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class ChangesTest(unittest.TestCase):
    def setUp(self):
        self.sources = {k: {'pages': ['收入 120 100 90 0 -100 1.2 25.42 26.53 999 2024 2023 重述确认']}
                        for k in ('new', 'old')}
        self.data = {'company': '示例公司', 'code': '000000',
                     'current_period': ['2024-01-01', '2024-12-31'],
                     'prior_period': ['2023-01-01', '2023-12-31'], 'documents': {}, 'metrics': []}
        for k, period in [('new', self.data['current_period']), ('old', self.data['prior_period'])]:
            self.data['documents'][k] = dict(source_id=k, company='示例公司', code='000000', report_type='annual', period=period)
        m = dict(id='revenue', label='收入', kind='flow', comparable=True)
        for k, v, doc, period in [('current','120','new',self.data['current_period']),
                                   ('baseline','100','new',self.data['prior_period']),
                                   ('prior_original','100','old',self.data['prior_period'])]:
            m[k] = dict(value=v, unit='元', currency='CNY', scope='consolidated', period=period,
                        source=dict(doc=doc, page=1, quote=self.sources[doc]['pages'][0]))
        self.data['metrics'] = [m]
        self.m = m

    def row(self):
        return c.calculate(self.data, self.sources)['metrics'][0]

    def test_growth(self):
        self.assertEqual(self.row()['change_pct'], '20.0')
        self.assertEqual(self.row()['change'], '20')

    def test_units_normalize(self):
        self.m['current'].update(value='1.2', unit='万元')
        self.assertEqual(self.row()['normalized_current'], '12000.0')
        self.assertEqual(c.number(self.row()['change_pct']), c.number('11900'))

    def test_nonpositive_base(self):
        for base in ['0', '-100']:
            with self.subTest(base=base):
                self.m['baseline']['value'] = self.m['prior_original']['value'] = base
                self.assertIsNone(self.row()['change_pct'])
                self.assertEqual(self.row()['status'], '可比')

    def test_million_yuan(self):
        for key in ('current', 'baseline', 'prior_original'):
            self.m[key]['unit'] = '百万元'
        row = self.row()
        self.assertEqual(row['normalized_current'], '120000000')
        self.assertEqual(row['change'], '20000000')
        self.assertEqual(row['normalized_unit'], '元')
        self.assertEqual(row['change_pct'], '20.0')

    def test_percentage_points(self):
        self.m['kind'] = 'ratio'
        for key, val in [('current','25.42'),('baseline','26.53'),('prior_original','26.53')]:
            self.m[key].update(value=val, unit='percent')
        self.assertEqual(self.row()['change'], '-1.11')
        self.assertEqual(self.row()['change_unit'], '百分点')
        self.assertIsNone(self.row()['change_pct'])

    def test_missing_not_zero(self):
        self.m['baseline']['value'] = None
        self.assertEqual(self.row()['status'], '未核实')
        self.assertIsNone(self.row()['change'])

    def test_scope_currency_and_unit_conflicts(self):
        for key, value in [('scope','parent'),('currency','USD'),('unit','percent')]:
            with self.subTest(key=key):
                original = self.m['baseline'][key]
                self.m['baseline'][key] = value
                self.assertEqual(self.row()['status'], '未核实')
                self.m['baseline'][key] = original

    def test_unconfirmed_restatement(self):
        self.m['prior_original']['value'] = '90'
        self.assertEqual(self.row()['status'], '未核实')
        self.assertEqual(self.row()['restatement_delta'], '10')
        self.assertIsNone(self.row()['change'])

    def test_confirmed_restatement_uses_new_base(self):
        self.m['prior_original']['value'] = '90'
        self.m['restatement'] = {'confirmed': True, 'source': {'doc':'new','page':1,'quote':'重述确认'}}
        self.assertEqual(self.row()['change_pct'], '20.0')
        self.assertEqual(self.row()['status'], '可比（已核实重述）')

    def test_wrong_evidence(self):
        for patch in [{'page':2}, {'quote':'不存在的原文'}, {'quote':'收入 999'}]:
            with self.subTest(patch=patch):
                original=copy.deepcopy(self.m['current']['source'])
                self.m['current']['source'].update(patch)
                self.assertEqual(self.row()['status'], '未核实')
                self.m['current']['source']=original

    def test_period_mismatch(self):
        self.m['current']['period'] = ['2024-07-01','2024-12-31']
        self.assertEqual(self.row()['status'], '未核实')

    def test_wrong_company_and_report_type(self):
        for key, value in [('code','111111'),('report_type','half-year')]:
            with self.subTest(key=key):
                original=self.data['documents']['old'][key]
                self.data['documents']['old'][key]=value
                with self.assertRaises(ValueError): c.calculate(self.data,self.sources)
                self.data['documents']['old'][key]=original

    def test_source_role_mismatch(self):
        self.m['prior_original']['source']['doc']='new'
        self.assertEqual(self.row()['status'],'未核实')

    def test_nonfinite_number(self):
        for v in ('NaN','Infinity',True):
            with self.assertRaises(ValueError): c.number(v)

    def test_stock_requires_asof_date(self):
        self.m['kind']='stock'
        self.assertEqual(self.row()['status'],'未核实')
        for k in ('current','baseline','prior_original'):
            self.m[k]['period']=[self.m[k]['period'][1]]
        self.assertEqual(self.row()['status'],'可比')

    def test_incomparable_requires_explicit_confirmation(self):
        self.m['comparable']=False
        self.assertEqual(self.row()['status'],'未核实')


if __name__ == '__main__':
    unittest.main()
