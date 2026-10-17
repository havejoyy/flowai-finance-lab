"""Determinism, reference timing, raw formulas and fail-closed local adapter."""
import importlib.util
import json
import os
import struct
import sys
import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]/'skills/panic-pressure-index/scripts'
sys.path.insert(0, str(SCRIPTS))
from panic_pressure import compute, digest, validate_factors
from calculator import calculate_score, calculate_raw, percentile
from methodology import METHOD, METHOD_HASH


def calendar_days(n):
    days=[]
    d=date(2024,1,1)
    while len(days)<n:
        if d.weekday()<5: days.append(d.isoformat())
        d+=timedelta(days=1)
    return days


def raw(day):
    boards={b:dict(expected=10,received=10,coverage='1',historyReceived=10,
                  regimeReceived=10,limitExpected=10,limitReceived=10)
            for b in ('MAIN','CHINEXT','STAR')}
    return dict(status='READY',rawStatus='READY',tradeDate=day,methodHash=METHOD_HASH,
                factors={c:dict(rawValue='0.1',canonicalValue='0.1000000000',numerator=3,denominator=30)
                         for c in METHOD['factors']},
                coverage=dict(expected=30,received=30,daily='1',history='1',regime='1',limit='1',boards=boards),
                warnings=[],exclusions={})


class FormulaTests(unittest.TestCase):
    def test_ties_and_zero(self):
        self.assertEqual(percentile('0.1',['0.1']*252),50)
        self.assertEqual(percentile('0',['0']*252),0)
        self.assertEqual(percentile('1',['0.1']*252),100)
        self.assertIsNone(percentile('0.1',['0.1']*251))

    def test_timing_and_warmup(self):
        days=calendar_days(300)
        refs=[dict(raw(d),id=d) for d in days]
        result=calculate_score(raw(days[252]), days, refs)
        self.assertEqual(result['score'],50)
        self.assertTrue(all(v==252 for v in result['referenceCounts'].values()))
        self.assertTrue(all(r['tradeDate']<days[252] for r in result['referenceManifest']))
        self.assertIsNone(calculate_score(raw(days[251]),days,refs)['score'])
        self.assertIsNone(calculate_score(raw(days[-1]),days,refs[:252])['score'])

    def test_duplicate_reference(self):
        days=calendar_days(253)
        refs=[dict(raw(d),id=d) for d in days[:-1]]
        with self.assertRaisesRegex(ValueError,'AMBIGUOUS'):
            calculate_score(raw(days[-1]),days,refs+[refs[0]])

    def test_raw_market_factors(self):
        days=calendar_days(121); target=days[-1]
        prices={d:'100' for d in days}; prices[target]='90'
        securities=[]
        for b in ('MAIN','CHINEXT','STAR'):
            securities.append(dict(securityId=b,stateVerified=True,tradeDate=target,exchange='SSE',
              assetType='A_SHARE',board=b,listedSessions=120,listed=True,st=False,delisting=False,
              suspended=False,regime='LIMITED',downLimit='90',tickSize='.01',
              history={d:dict(close=prices[d],preClose='100',factor='1') for d in days}))
        r=calculate_raw(target,days,prices,securities,universe_verified=True)
        self.assertEqual(r['status'],'READY')
        self.assertEqual(r['factors']['F1']['rawValue'],Decimal('.1'))
        self.assertAlmostEqual(float(r['factors']['F2']['rawValue']),(.01/10*252)**.5)
        for c in ('F3','F4','F5','F6'): self.assertEqual(r['factors'][c]['rawValue'],1)
        securities[0]['stateVerified']=False
        self.assertEqual(calculate_raw(target,days,prices,securities,universe_verified=True)['status'],'INCOMPLETE')


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.data=self.root/'data'; self.data.mkdir()
        self.bundle=self.root/'bundle'; self.bundle.mkdir()
        self.days=calendar_days(254)
        header=str(dict(descr='<i8',fortran_order=False,shape=(len(self.days),))).encode()+b'\n'
        content=b'\x93NUMPY\x01\x00'+struct.pack('<H',len(header))+header
        content+=b''.join(struct.pack('<q',int(d.replace('-',''))) for d in self.days)
        self.cal=self.bundle/'trading_dates.npy'; self.cal.write_bytes(content)
        import hashlib
        bm={'trading_dates.npy':dict(bytes=len(content),sha256=hashlib.sha256(content).hexdigest())}
        (self.bundle/'manifest.json').write_text(json.dumps(bm))
        manifest=dict(bundle=digest(bm),method=METHOD_HASH)
        (self.data/'manifest.json').write_text(json.dumps(manifest))
        mh=digest(manifest)
        for d in self.days:
            r=raw(d)
            r.update(id=digest(dict(inputManifest=mh,day=d)),inputManifestHash=mh,
                     sourceVersion='SYNTHETIC_TEST_ONLY',historyMode='SYNTHETIC',
                     methodVersion=METHOD['version'],universeVersion=METHOD['universeVersion'],score=None)
            r['contentHash']=digest(r)
            (self.data/(d+'.json')).write_text(json.dumps(r))
    def tearDown(self): self.tmp.cleanup()
    def run_score(self, day='latest'):
        return compute(self.data,self.cal,day,as_of='2026-10-17')
    def test_repeatable_score_and_hash(self):
        first=self.run_score(); second=self.run_score()
        self.assertEqual(first['score'],50)
        self.assertEqual(first['resultHash'],second['resultHash'])
        self.assertEqual(first['referenceCounts']['F1'],253)
    def test_explicit_missing_date_not_substituted(self):
        with self.assertRaisesRegex(ValueError,'REQUESTED_DATE_UNAVAILABLE'): self.run_score('2026-10-16')
    def test_warmup_has_no_partial_scores(self):
        r=self.run_score(self.days[251]); self.assertIsNone(r['score']); self.assertEqual(r['scores'],{})
    def test_content_corruption_rejected(self):
        p=self.data/(self.days[-1]+'.json'); r=json.loads(p.read_text()); r['score']='99'; p.write_text(json.dumps(r))
        with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'): self.run_score()
    def test_calendar_corruption_rejected(self):
        self.cal.write_bytes(self.cal.read_bytes()+b'x')
        with self.assertRaisesRegex(ValueError,'CALENDAR_HASH_MISMATCH'): self.run_score()
    def test_coverage_and_ratio_gates(self):
        r=raw(self.days[-1]); r['coverage']['history']='.8'
        with self.assertRaisesRegex(ValueError,'INVALID_COVERAGE'): validate_factors(r)
        r=raw(self.days[-1]); r['factors']['F3']['numerator']=15
        with self.assertRaisesRegex(ValueError,'FACTOR_RATIO_MISMATCH'): validate_factors(r)


@unittest.skipUnless(os.environ.get('PANIC_REAL_DATA'),'Set PANIC_REAL_DATA for local real-data integration')
class RealDataTest(unittest.TestCase):
    def test_matches_microapp(self):
        base=Path(os.environ['PANIC_REAL_DATA'])
        r=compute(base/'backfill-v2-20261016',base/'rqbundle-202610/extracted/trading_dates.npy','2026-09-30',as_of='2026-10-17')
        self.assertTrue(r['cachedScoreMatches'])
        self.assertEqual(r['score'],Decimal('66.54846335697399527186761229314420803783'))
        self.assertEqual(r['referenceCounts']['F1'],423)
        self.assertEqual(r['coverage']['expected'],4972)


if __name__=='__main__': unittest.main()
