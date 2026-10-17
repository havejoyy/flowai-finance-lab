#!/usr/bin/env python3
"""Recompute CN_PANIC_V1 from version-selected immutable local factor snapshots."""
import argparse
import ast
import hashlib
import json
import struct
import sys
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from calculator import calculate_score, canonical, number, trading_window
from methodology import METHOD, METHOD_HASH


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def load_calendar(path, manifest):
    """Load pinned integer NPY without NumPy, pickle, or arbitrary-code loading."""
    path = Path(path)
    meta = read_json(path.parent / 'manifest.json')
    if digest(meta) != manifest['bundle']:
        raise ValueError('CALENDAR_BUNDLE_MISMATCH')
    data = path.read_bytes()
    entry = meta[path.name]
    if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
        raise ValueError('CALENDAR_HASH_MISMATCH')
    if data[:8] != b'\x93NUMPY\x01\x00':
        raise ValueError('UNSUPPORTED_NPY_FORMAT_EXPECT_V1_INT64')
    end = 10 + struct.unpack('<H', data[8:10])[0]
    header = ast.literal_eval(data[10:end].decode('ascii').strip())
    if header['descr'] not in ('<i8', '>i8') or header['fortran_order'] or len(header['shape']) != 1:
        raise ValueError('UNSUPPORTED_CALENDAR_ARRAY')
    count = header['shape'][0]
    if len(data) - end != count * 8:
        raise ValueError('INVALID_CALENDAR_ARRAY_LENGTH')
    calendar = [datetime.strptime(str(v[0]), '%Y%m%d').date().isoformat()
                for v in struct.iter_unpack(header['descr'][0] + 'q', data[end:])]
    trading_window(calendar, calendar[-1], 1)
    return calendar


def validate_factors(row):
    """Recheck stored raw-value/coverage gates; hashes are integrity, not attestation."""
    if row.get('rawStatus') != 'READY':
        return
    c = row['coverage']
    for key, threshold in [('daily', '.98'), ('history', '.95'), ('regime', '.99'), ('limit', '.99')]:
        if not Decimal(threshold) <= number(c[key]) <= 1:
            raise ValueError('INVALID_COVERAGE:' + key)
    if set(c['boards']) != {'MAIN', 'CHINEXT', 'STAR'}:
        raise ValueError('MISSING_BOARD_COVERAGE')
    for counts in c['boards'].values():
        for numerator, denominator, threshold in [('received','expected','.95'),
                ('historyReceived','received','.95'), ('regimeReceived','received','.99'),
                ('limitReceived','limitExpected','.99')]:
            n, d = counts[numerator], counts[denominator]
            if type(n) is not int or type(d) is not int or not 0 <= n <= d:
                raise ValueError('INVALID_BOARD_COUNTS')
            if d and Decimal(n) / d < Decimal(threshold):
                raise ValueError('INVALID_BOARD_COVERAGE')
    for code in METHOD['factors']:
        f = row['factors'][code]
        x = number(f['rawValue'])
        if x < 0 or (code != 'F2' and x > 1) or canonical(x) != number(f['canonicalValue']):
            raise ValueError('INVALID_FACTOR:' + code)
        if code in ('F3','F4','F5','F6'):
            n, d = f['numerator'], f['denominator']
            if type(n) is not int or type(d) is not int or not 0 <= n <= d or d == 0:
                raise ValueError('INVALID_FACTOR_COUNTS:' + code)
            if canonical(Decimal(n) / d) != canonical(x):
                raise ValueError('FACTOR_RATIO_MISMATCH:' + code)
    if row.get('warnings'):
        raise ValueError('READY_WITH_WARNINGS')


def load_rows(folder, manifest, calendar, as_of):
    mh = digest(manifest)
    rows = []
    for path in sorted(Path(folder).glob('????-??-??.json')):
        day = path.stem
        date.fromisoformat(day)
        if day > as_of:
            continue
        row = read_json(path)
        if row.get('contentHash') != digest({k:v for k,v in row.items() if k != 'contentHash'}):
            raise ValueError('CANDIDATE_HASH_MISMATCH:' + day)
        if (row.get('tradeDate') != day or day not in calendar or
                row.get('methodHash') != METHOD_HASH or row.get('methodVersion') != METHOD['version'] or
                row.get('universeVersion') != METHOD['universeVersion'] or
                row.get('inputManifestHash') != mh or
                row.get('id') != digest({'inputManifest': mh, 'day': day})):
            raise ValueError('CANDIDATE_VERSION_MISMATCH:' + day)
        validate_factors(row)
        rows.append(row)
    if not rows:
        raise ValueError('NO_DATED_SNAPSHOTS')
    if len({(r['sourceVersion'], r['historyMode']) for r in rows}) != 1:
        raise ValueError('MIXED_SOURCE_VERSIONS')
    return rows


def compute(folder, calendar_path, target='latest', as_of=None):
    as_of = as_of or datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    manifest = read_json(Path(folder) / 'manifest.json')
    if manifest['method'] != METHOD_HASH:
        raise ValueError('METHOD_MISMATCH')
    calendar = load_calendar(calendar_path, manifest)
    rows = load_rows(folder, manifest, calendar, as_of)
    if target != 'latest':
        date.fromisoformat(target)
        if target > as_of:
            raise ValueError('FUTURE_DATE_NOT_ALLOWED')
    candidates = list(reversed(rows)) if target == 'latest' else [r for r in rows if r['tradeDate'] == target]
    if not candidates:
        raise ValueError('REQUESTED_DATE_UNAVAILABLE:' + target + ';latestSnapshot=' + rows[-1]['tradeDate'])
    refs = [{**r, 'status': r['rawStatus']} for r in rows]
    result = None
    for row in candidates:
        raw = {k: row[k] for k in ('tradeDate','factors','coverage','exclusions','warnings','methodHash')}
        raw['status'] = row['rawStatus']
        result = calculate_score(raw, calendar, refs)
        if result['status'] == 'READY' or target != 'latest':
            break
    result.update(requestedDate=target, checkedOn=as_of, latestSnapshotDate=rows[-1]['tradeDate'],
                  dataAgeCalendarDays=(date.fromisoformat(as_of)-date.fromisoformat(result['tradeDate'])).days,
                  methodVersion=METHOD['version'], universeVersion=METHOD['universeVersion'],
                  sourceVersion=row['sourceVersion'], historyMode=row['historyMode'],
                  inputManifestHash=digest(manifest), sourceContentHash=row['contentHash'],
                  recomputed=True, calculationScope='SIX_FACTORS_TO_SCORE',
                  cachedScoreMatches=(row.get('score') is not None and result['score'] is not None and
                                      number(row['score']) == result['score']))
    if result['status'] != 'READY':
        # A partial factor rank is not a publishable score.
        result.update(score=None, scores={}, dimensions={})
    result['resultHash'] = digest({k:result[k] for k in ('tradeDate','score','scores','dimensions','methodHash','inputManifestHash','sourceContentHash','referenceCounts')})
    return result


def formatted(value):
    return str(number(value).quantize(Decimal('.1'), rounding=ROUND_HALF_UP))


def markdown(r):
    lines = ['# A股恐慌压力指标（试验版）', '', f"数据日：{r['tradeDate']}｜查询日：{r['checkedOn']}｜状态：{r['status']}"]
    if r['score'] is None:
        lines += ['', '当前数据不能生成完整分数。需补齐当日六因子或至少252个有效历史参考日。']
    else:
        lines += ['', f"**恐慌压力：{formatted(r['score'])} / 100**", '', '| 分项 | 分数 | 看什么 |', '|---|---:|---|']
        for key, label, meaning in [('intensity','强度','下跌有多深'),('breadth','扩散','下跌有多广'),('selling','抛压','极端下跌有多少')]:
            lines.append(f"| {label} | {formatted(r['dimensions'][key])} | {meaning} |")
        lines += ['', '| 因子 | 原始值 | 历史压力分 | 参考日数 |', '|---|---:|---:|---:|']
        names = {'F1':'60日回撤','F2':'10日下行波动','F3':'下跌占比','F4':'新低占比','F5':'跌停占比','F6':'大跌占比'}
        for code in METHOD['factors']:
            lines.append(f"| {code} {names[code]} | {r['factors'][code]['canonicalValue']} | {formatted(r['scores'][code])} | {r['referenceCounts'][code]} |")
        highest = max(r['dimensions'], key=r['dimensions'].get)
        labels={'intensity':'强度','breadth':'扩散','selling':'抛压'}
        lines += ['', f"本次最高分项为{labels[highest]}；这只是三个分项的相对比较。"]
    if r['dataAgeCalendarDays']:
        lines += ['', f"数据距查询日 {r['dataAgeCalendarDays']} 个自然日；这是最近可计算历史快照，不代表今天盘面。"]
    lines += ['', f"口径：{r['methodVersion']}；来源版本：{r['sourceVersion']}；历史模式：{r['historyMode']}。",
              '本次从已校验的六因子快照重算历史排名和总分，未重新采集行情或复核供应商原始行情。',
              '高分表示所选市场行为相对历史更有压力，不是恐慌人数占比、下跌概率或买卖信号。',
              f"结果校验值：`{r['resultHash']}`"]
    return '\n'.join(lines) + '\n'


def main():
    if not any(arg in ('--local', '--data-dir', '--calendar', '--config') for arg in sys.argv[1:]):
        from run_public import main as public_main
        return public_main()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local', action='store_true', help='Use legacy precomputed local snapshots')
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1]/'config.local.json')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--calendar', type=Path, help='Pinned trading_dates.npy beside its manifest.json')
    parser.add_argument('--date', default='latest', help='latest (latest computable day) or YYYY-MM-DD')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        config = read_json(args.config) if args.config.exists() else {}
        folder = args.data_dir or config.get('data_dir')
        calendar = args.calendar or config.get('calendar')
        if not folder or not calendar:
            raise ValueError('DATA_NOT_CONFIGURED: set --data-dir and --calendar or config.local.json')
        result = compute(folder, calendar, args.date)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str) if args.json else markdown(result))
        return 0 if result['status'] == 'READY' else 2
    except (ValueError, KeyError, TypeError, OSError, ArithmeticError, struct.error) as exc:
        print(json.dumps({'status':'UNAVAILABLE', 'score':None, 'error':str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
