"""Meaningful retry, corrupt/empty quote and desktop isolation contract tests."""
import copy,json,ssl,tempfile,unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from engine import Store,CN
from data_recovery import retry_data,error_info
from screening_data import SourceUnavailable,repair_snapshot_gaps,demo_snapshot
from market_store import MarketStore

class RecoveryTests(unittest.TestCase):
    def test_transient_recovers_and_attempts_are_bounded(self):
        events=[];attempts=[]
        def transient():
            attempts.append(1)
            if len(attempts)<2:raise SourceUnavailable('断开连接')
            return {'real':'payload'}
        self.assertEqual(retry_data(transient,'public',events.append,sleep=lambda _:None),{'real':'payload'})
        self.assertEqual(events[0]['attempt'],2)
        failed=[]
        def offline():failed.append(1);raise SourceUnavailable('持续离线')
        with self.assertRaises(SourceUnavailable):retry_data(offline,'public',sleep=lambda _:None)
        self.assertEqual(len(failed),2)
    def test_bad_data_auth_and_tls_do_not_retry(self):
        for error in [ValueError('行情日期不一致'),HTTPError('https://example.test',403,'denied',{},None),ssl.SSLCertVerificationError('CERTIFICATE_VERIFY_FAILED')]:
            seen=[]
            def fail():seen.append(1);raise error
            with self.assertRaises(type(error)):retry_data(fail,'public',sleep=lambda _:None)
            self.assertEqual(len(seen),1)
        self.assertEqual(error_info(ssl.SSLCertVerificationError('bad'))['category'],'certificate')
    def test_gap_recheck_never_uses_old_date_or_zero(self):
        s=demo_snapshot();s['as_of']='2026-10-30';r=s['rows'][0];r['code']='600000';r.update(date='',quote_at='',close=None,pct=None,amount=None,eod=False)
        # All other rows should be valid to isolate one missing quote.
        for row in s['rows'][1:]:row.update(date=s['as_of'],eod=True)
        parts=['0']*33;parts[0]='公开测试证券';parts[2]='10';parts[3]='11';parts[9]='200000000';parts[30]='2026-10-30';parts[31]='15:30:00'
        body=lambda p:('var hq_str_sh'+r['code']+'="'+','.join(p)+'";').encode('gb18030')
        now=datetime(2026,10,31,20,tzinfo=CN)
        repaired=repair_snapshot_gaps(s,reader=lambda _:body(parts),now=now)
        self.assertEqual(repaired['rows'][0]['close'],11);self.assertEqual(repaired['evidence']['quote_gap_recheck']['recovered'],1)
        old=parts[:];old[30]='2026-10-29'
        bad=repair_snapshot_gaps(s,reader=lambda _:body(old),now=now)
        self.assertIsNone(bad['rows'][0]['close']);self.assertEqual(bad['evidence']['quote_gap_recheck']['remaining'],1)
        self.assertIsNone(s['rows'][0]['close'])
    def test_manager_records_retry_without_stale_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=MarketStore(Store(tmp));calls=[]
            def bad():calls.append(1);raise SourceUnavailable('离线')
            with patch('data_recovery.time.sleep'),self.assertRaises(SourceUnavailable):manager.retry(bad,'calendar')
            self.assertEqual(manager.stats['automatic_retries'],1);self.assertEqual(manager.recovery_events[0]['resource'],'calendar')
            self.assertIsNone(manager.cached('five.calendar','2026-10'))
    def test_existing_session_rejects_non_local_url(self):
        from desktop_backend import existing_session
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'desktop-session.json').write_text(json.dumps({'url':'http://untrusted.example:99','instance':'x','pid':1}))
            self.assertIsNone(existing_session(p))
if __name__=='__main__':unittest.main()
