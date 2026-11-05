"""Coverage is a read-only view, not a claim that the whole market was screened."""
import copy
import unittest
from report_scope import report_scope, quote_gap_reason


class ReportScopeTests(unittest.TestCase):
    def item(self):
        return dict(strategy={'template': 'youzi-five-layer-breakout-v1'}, snapshot={'rows': [
            dict(code='000001', name='缺报价', date='2026-10-30', eod=True, close=None, pct=None, amount=0),
            dict(code='000002', name='缺历史', date='2026-10-30', eod=True, close=10, pct=1, amount=100)]}, report=dict(
                as_of='2026-10-30', counts=dict(source=5221, excluded=234, market=4984, passed=2,
                                               prefilter=135, checked=10, deferred=125, history_failed=1),
                unavailable=[dict(code='000001', name='缺报价', reason='收盘报价无效')],
                failures={'000002': '历史不足61个交易日'},
                evaluations=[dict(stock=dict(code='000002', name='缺历史'), status='待核验',
                                  reason='历史不足61个交易日')],
                five_layer=dict(coverage=dict(eligible_expected=4989, quote_valid=4984, missing_eligible=[
                    dict(code='000001', name='缺报价', reason='收盘报价无效'),
                    dict(code='000003', name='目录外', reason='未在行情目录中')]),
                    sources=[dict(dataset='benchmark', status='待核验', required=True, reason='指数日期错位')],
                    stages=[dict(status='未进入')])) )

    def test_overlapping_gaps_and_outside_directory_are_not_added_twice(self):
        item = self.item()
        before = copy.deepcopy(item)
        scope = report_scope(item)
        self.assertEqual(scope['gap_count'], 3)
        self.assertEqual(scope['source_invalid'], 3)
        self.assertEqual([g['code'] for g in scope['gaps']], ['000001', '000002', '000003'])
        self.assertIn('收盘价缺失', scope['gaps'][0]['reasons'][0])
        self.assertIn('成交额不大于零', scope['gaps'][0]['reasons'][0])
        self.assertEqual(scope['gaps'][2]['reasons'], ['未在行情目录中'])
        self.assertEqual(scope['gaps'][1]['name'], '缺历史')
        self.assertEqual(len(scope['resource_gaps']), 1)
        self.assertTrue(any('125' in n and '不计为不满足' in n for n in scope['notes']))
        self.assertTrue(any('后续层级未进入' in n for n in scope['notes']))
        self.assertEqual(item, before)

    def test_legacy_report_does_not_invent_complete_exchange_coverage(self):
        item = self.item()
        del item['report']['five_layer']
        item['strategy']['template'] = 'volume'
        item['report']['industry_data'] = {'unknown': [{'code': '000004', 'reason': '行业缺失'}]}
        scope = report_scope(item)
        self.assertEqual(scope['gap_count'], 2)
        self.assertFalse(any('合资格名录' in n for n in scope['notes']))
        self.assertTrue(any('不是全市场完整筛选' in n for n in scope['notes']))

    def test_industry_gap_only_in_required_strategy_and_exclusions_not_counted(self):
        item = self.item()
        item['report']['excluded'] = [dict(code='000005')]
        item['report']['industry_data'] = dict(unknown=[dict(code='000004', name='缺行业', reason='成分未覆盖'),
                                                      dict(code='000005', reason='基础已排除')], conflicts=[])
        scope = report_scope(item)
        self.assertEqual(scope['gap_count'], 4)
        self.assertNotIn('000005', [g['code'] for g in scope['gaps']])

    def test_pending_indicator_without_history_error_is_visible(self):
        item = self.item()
        item['report']['evaluations'].append(dict(stock=dict(code='000006', name='缺公告'), status='待核验',
            checks=[dict(name='公告目录', status='待核验', reason='发布时间缺失')]))
        scope = report_scope(item)
        self.assertEqual(scope['gap_count'], 4)
        self.assertEqual(scope['gaps'][-1]['reasons'], ['发布时间缺失'])

    def test_industry_failure_reason_is_display_text(self):
        item = self.item()
        item['report']['industry_data'] = {'failed_groups': {'科技': {'reason': '连接中断'}}}
        self.assertEqual(report_scope(item)['resource_gaps'][-1]['reason'], '连接中断')

    def test_field_specific_date_and_amount_reasons(self):
        reason = quote_gap_reason(dict(eod=False, date='2026-10-29', close=10, pct=None, amount=0), '2026-10-30')
        self.assertIn('报价日期 2026-10-29', reason)
        self.assertIn('涨跌幅缺失', reason)
        self.assertNotIn('停牌', reason)


if __name__ == '__main__':
    unittest.main()
