"""Public workflow safety, independent method scope, and temporal scoring tests."""
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from datetime import date,timedelta

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/panic-pressure-index/scripts'))
from public_source import months,extract,acquire,FILES,download
from public_compute import select_universe,cache_read,score
from public_methodology import METHOD,METHOD_HASH,engine
from methodology import METHOD_HASH as LEGACY_HASH
from calculator import calculate_score as legacy_score
from panic_pressure import digest


class PublicSourceTest(unittest.TestCase):
    def test_month_fallback_year_boundary(self):
        self.assertEqual(list(months(date(2026,1,1))),['202601','202512','202511'])
    def test_no_offline_cache_is_explicit(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError,'NO_OFFLINE_CACHE'): acquire(root,date.today(),offline=True)
    def test_archive_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'bad.tar.bz2'
            with tarfile.open(p,'w:bz2') as tar:
                info=tarfile.TarInfo('../stocks.h5');info.size=1;tar.addfile(info,io.BytesIO(b'x'))
            with self.assertRaisesRegex(ValueError,'UNSAFE'): extract(p,Path(root)/'out')
            self.assertFalse((Path(root)/'stocks.h5').exists())
    def test_missing_archive_members_not_complete(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'missing.tar.bz2'
            with tarfile.open(p,'w:bz2'): pass
            with self.assertRaisesRegex(ValueError,'MISSING_BUNDLE_FILES'):extract(p,Path(root)/'out')
    def test_valid_extraction_hash_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'good.tar.bz2'
            with tarfile.open(p,'w:bz2') as tar:
                for name in FILES:
                    info=tarfile.TarInfo('bundle/'+name); info.size=1;tar.addfile(info,io.BytesIO(b'x'))
            manifest=extract(p,Path(root)/'out')
            self.assertEqual(set(manifest),FILES)
            self.assertTrue(all(m['bytes']==1 for m in manifest.values()))
    def test_truncated_download_not_promoted(self):
        with tempfile.TemporaryDirectory() as root:
            target=Path(root)/'bundle.tar.bz2'
            remote=dict(url='https://example.invalid',bytes=10)
            with patch('public_source.urlopen',side_effect=lambda *a,**k:io.BytesIO(b'short')),patch('public_source.time.sleep'):
                with self.assertRaisesRegex(ValueError,'TRUNCATED_DOWNLOAD'):download(remote,target)
            self.assertFalse(target.exists())
    def test_corrupt_derived_cache_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'cache.json';p.write_text(json.dumps(dict(scope={},rows=[],contentHash='bad')))
            with self.assertRaisesRegex(ValueError,'CORRUPTED'):cache_read(p,{})
    def test_source_rechecked_and_unchanged_cache_reused(self):
        with tempfile.TemporaryDirectory() as root:
            import hashlib
            remote=dict(month='202610',url='https://example.invalid',bytes=2,etag='same',lastModified='same')
            key=hashlib.sha256(json.dumps(remote,sort_keys=True).encode()).hexdigest()[:20]
            directory='bundle-202610-'+key;path=Path(root)/directory;path.mkdir()
            (path/'manifest.json').write_text('{}');(path/'download.json').write_text(json.dumps(dict(remote,sha256='test')))
            with patch('public_source.discover',return_value=remote) as check,patch('public_source.download') as fetch:
                result,info=acquire(root,date(2026,10,17))
                check.assert_called_once();fetch.assert_not_called();self.assertEqual(result,path)
                self.assertTrue(info['networkChecked'])


class PublicCalculationTest(unittest.TestCase):
    def test_method_namespace_cannot_change_legacy(self):
        self.assertNotEqual(METHOD_HASH,LEGACY_HASH)
        import calculator
        self.assertEqual(calculator.METHOD_HASH,LEGACY_HASH)
        self.assertEqual(engine.METHOD_HASH,METHOD_HASH)
    def test_public_score_no_future_or_same_day_refs(self):
        days=[(date(2024,1,1)+timedelta(days=n)).isoformat() for n in range(260)]
        rows=[dict(tradeDate=d,status='READY',methodHash=METHOD_HASH,id=d,
                   factors={c:dict(canonicalValue='.1') for c in METHOD['factors']},coverage={}) for d in days]
        prepared=dict(rows=rows,scope=dict(end=days[-1],universeHash='u'),contentHash='c',universeCount=1000)
        source=dict(month='202410',url='https://example.invalid',sha256='a',networkChecked=True)
        r=score(prepared,days,days[252],'2026-10-17',source)
        self.assertEqual(r['score'],50)
        self.assertEqual(r['referenceCounts']['F1'],252)
        self.assertTrue(all(x['tradeDate']<days[252] for x in r['referenceManifest']))
        with self.assertRaisesRegex(ValueError,'REQUESTED_DATE_UNAVAILABLE'):score(prepared,days,'2026-10-16','2026-10-17',source)
    def test_fixed_cohort_excludes_delisted_and_cdr(self):
        common=dict(type='CS',exchange='XSHG',status='Active',de_listed_date='0000-00-00',
                    special_type='Normal',listed_date='2000-01-01',symbol='示例')
        rows=[dict(common,order_book_id=f'600{i:03}.XSHG') for i in range(1000)]
        rows += [dict(common,order_book_id='689009.XSHG'),
                 dict(common,order_book_id='601001.XSHG',status='Delisted'),
                 dict(common,order_book_id='601002.XSHG',symbol='退市示例')]
        result=select_universe(rows,'2026-09-30')
        self.assertEqual(len(result),1000)


if __name__=='__main__':unittest.main()
