import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from engine import Store
from data_update import DataUpdates
from market_store import MarketStore
from screening import ScreeningStore
from test_market_store import fixtures
from test_industry_data import industry_fixture


class DataUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        ScreeningStore(self.store)
        self.updates = DataUpdates(self.store)
        self.clock = lambda: datetime(2026, 10, 31, 8, tzinfo=timezone.utc)
        patcher = patch('data_update.MarketStore', side_effect=lambda *a, **kw: MarketStore(*a, now=self.clock, **kw))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_refresh_fetches_again_and_saves_without_screening(self):
        fetch = Mock(side_effect=fixtures)
        industry = Mock(side_effect=industry_fixture)
        for _ in range(2):
            result = self.updates.execute(self.updates.start(), fetch, industry)
            self.assertEqual(result['state'], 'COMPLETED')
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(industry.call_count, 2)
        self.assertEqual(result['as_of'], '2026-10-30')
        self.assertEqual(DataUpdates(self.store).get()['id'], result['id'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM screen_runs').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM market_batches WHERE kind='snapshot'").fetchone()[0], 2)

    def test_failure_preserves_previous_data_and_is_not_success(self):
        self.updates.execute(self.updates.start(), fixtures, industry_fixture)
        before = MarketStore(self.store, now=self.clock).cached('snapshot', 'universe')['id']
        result = self.updates.execute(self.updates.start(), Mock(side_effect=ValueError('行情日期无效')), industry_fixture)
        self.assertEqual(result['state'], 'FAILED')
        self.assertEqual(result['as_of'], '')
        self.assertIn('行情日期无效', result['message'])
        self.assertEqual(MarketStore(self.store, now=self.clock).cached('snapshot', 'universe')['id'], before)

    def test_partial_quote_and_industry_failure_are_visible(self):
        snapshot = fixtures()
        snapshot['rows'][0]['amount'] = 0
        result = self.updates.execute(self.updates.start(), lambda: snapshot,
                                      Mock(side_effect=ValueError('行业数据不完整')))
        self.assertEqual(result['state'], 'PARTIAL')
        self.assertEqual(result['as_of'], '2026-10-30')
        self.assertIn('成交额不大于零', result['issues'][0]['reason'])
        self.assertEqual(result['issues'][1]['name'], '行业分类')
        self.assertIsNotNone(MarketStore(self.store, now=self.clock).cached('snapshot', 'universe'))

    def test_restart_marks_interrupted_and_does_not_fetch(self):
        item = self.updates.start()
        restarted = DataUpdates(self.store)
        restarted.recover()
        self.assertEqual(restarted.get(item['id'])['state'], 'INTERRUPTED')
        with self.assertRaises(ValueError):
            restarted.get('missing')


if __name__ == '__main__':
    unittest.main()
