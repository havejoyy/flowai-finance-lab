"""Deterministic, database-free CN_PANIC_V1 calculation on verified snapshots.

Inputs are version-selected upstream. No current stock name or price-table fallback.
All decimals enter from strings; no inference of suspension or price-limit regime.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from datetime import date
from functools import wraps
from methodology import METHOD, METHOD_HASH

D = Decimal
ZERO = D(0)
ONE = D(1)
QUANTUM = D('0.0000000001')
BOARDS = {'MAIN', 'CHINEXT', 'STAR'}


def fixed_precision(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        with localcontext() as context:
            context.prec = 40
            context.rounding = ROUND_HALF_UP
            return fn(*args, **kwargs)
    return run


def number(value, positive=False):
    try:
        result = D(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('INVALID_NUMBER') from None
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError('INVALID_NUMBER')
    return result


@fixed_precision
def canonical(value):
    return number(value).quantize(QUANTUM, rounding=ROUND_HALF_UP)


def ratio(n, d):
    return D(n) / D(d) if d else None


@fixed_precision
def percentile(value, references):
    """Caller must enforce dated reference window and coverage; no current observation."""
    if len(references) < METHOD['minimumReferences']:
        return None
    x = canonical(value)
    refs = [canonical(v) for v in references]
    if x < 0 or any(v < 0 for v in refs):
        raise ValueError('NEGATIVE_PRESSURE')
    if x == 0:
        return ZERO
    return D(100) * (sum(v < x for v in refs) + D('0.5') * sum(v == x for v in refs)) / len(refs)


def trading_window(calendar, target, count):
    if calendar != sorted(set(calendar)):
        raise ValueError('INVALID_CALENDAR')
    for value in calendar:
        date.fromisoformat(value)
    if target not in calendar:
        raise ValueError('NON_TRADING_DATE')
    end = calendar.index(target) + 1
    if end < count:
        raise ValueError('INSUFFICIENT_CALENDAR')
    return calendar[end-count:end]


@fixed_precision
def calculate_raw(target, calendar, benchmark, securities, *, universe_verified=False):
    """securities: all dated state rows with identity/stateVerified and history snapshots.

    state: exchange, assetType, board, listedSessions, listed, st, delisting,
    suspended (true only for verified full-day suspension), regime.
    history: {date: {close, preClose, factor}}, downLimit and tickSize on today's state.
    """
    result = {'status': 'INCOMPLETE', 'tradeDate': target, 'factors': {}, 'coverage': {},
              'exclusions': {}, 'warnings': [], 'methodHash': METHOD_HASH}
    excluded = result['exclusions']
    def exclude(reason):
        excluded[reason] = excluded.get(reason, 0) + 1
    if not universe_verified:
        result['warnings'].append('UNVERIFIED_UNIVERSE')
        return result
    try:
        window = trading_window(calendar, target, 61)
        closes = [number(benchmark[day], positive=True) for day in window[-60:]]
    except (ValueError, KeyError):
        result['warnings'].append('INCOMPLETE_INDEX_OR_CALENDAR')
        return result
    with localcontext() as context:
        context.prec = 40
        f1 = ONE - closes[-1] / max(closes)
        returns = [closes[i] / closes[i-1] - ONE for i in range(len(closes)-10, len(closes))]
        f2 = (sum(min(r, ZERO) ** 2 for r in returns) / 10).sqrt() * D(252).sqrt()
    seen = set()
    expected = []
    for row in securities:
        key = row.get('securityId')
        if not key or key in seen or row.get('stateVerified') is not True or row.get('tradeDate') != target:
            result['warnings'].append('INVALID_OR_DUPLICATE_SECURITY_STATE')
            return result
        seen.add(key)
        if not row.get('exchange') or not row.get('assetType'):
            result['warnings'].append('UNKNOWN_SECURITY_IDENTITY'); return result
        if row.get('exchange') not in {'SSE', 'SZSE'} or row.get('assetType') != 'A_SHARE':
            exclude('OUTSIDE_SCOPE'); continue
        if row.get('board') not in BOARDS or any(type(row.get(k)) is not bool for k in ('listed', 'st', 'delisting', 'suspended')):
            result['warnings'].append('UNKNOWN_SECURITY_STATE'); return result
        if not isinstance(row.get('listedSessions'), int) or row['listedSessions'] < 0:
            result['warnings'].append('UNKNOWN_LISTING_AGE'); return result
        if not row['listed'] or row['listedSessions'] < 120 or row['st'] or row['delisting']:
            exclude('UNIVERSE_EXCLUDED'); continue
        if row['suspended']:
            exclude('FULL_DAY_SUSPENDED'); continue
        expected.append(row)
    valid, low_eligible, limited = [], [], []
    board_quality = {board: {'historyReceived': 0, 'regimeReceived': 0, 'limitExpected': 0, 'limitReceived': 0} for board in BOARDS}
    regime_known = 0
    limited_expected = 0
    for row in expected:
        try:
            history = row['history']
            today = history[target]
            close, pre, factor = (number(today[k], positive=True) for k in ('close', 'preClose', 'factor'))
            valid.append((row, close, pre, factor))
        except (KeyError, ValueError):
            exclude('INVALID_OR_MISSING_DAILY'); continue
        try:
            prices = [number(history[d]['close'], positive=True) * number(history[d]['factor'], positive=True) / factor for d in window]
            # A stored price on a suspended day must not qualify as an observation.
            if any(history[d].get('suspended', False) for d in window):
                raise ValueError()
            low_eligible.append(prices[-1] < min(prices[:-1]))
            board_quality[row['board']]['historyReceived'] += 1
        except (KeyError, ValueError):
            exclude('INCOMPLETE_61_DAY_HISTORY')
        regime = row.get('regime')
        if regime in {'LIMITED', 'UNLIMITED'}:
            regime_known += 1
            board_quality[row['board']]['regimeReceived'] += 1
        if regime != 'LIMITED':
            exclude('NO_LIMIT' if regime == 'UNLIMITED' else 'UNKNOWN_LIMIT_REGIME'); continue
        limited_expected += 1
        board_quality[row['board']]['limitExpected'] += 1
        try:
            down, tick = number(row['downLimit'], positive=True), number(row['tickSize'], positive=True)
            amplitude = ONE - down / pre
            if not ZERO < amplitude < ONE or close < down:
                raise ValueError()
            # Reject off-tick prices rather than rounding malformed prices into a limit hit.
            if close % tick or down % tick:
                raise ValueError()
            limited.append((close == down, (ONE - close / pre) / amplitude >= D('0.5')))
            board_quality[row['board']]['limitReceived'] += 1
        except (KeyError, ValueError):
            exclude('INVALID_PRICE_LIMIT')
    coverage = result['coverage']
    coverage.update(expected=len(expected), received=len(valid), daily=ratio(len(valid), len(expected)),
                    history=ratio(len(low_eligible), len(valid)), regime=ratio(regime_known, len(valid)),
                    limit=ratio(len(limited), limited_expected), boards={})
    for board in sorted(BOARDS):
        n = sum(row['board'] == board for row, *_ in valid)
        d = sum(row['board'] == board for row in expected)
        coverage['boards'][board] = {'expected': d, 'received': n, 'coverage': ratio(n, d), **board_quality[board]}
    factors = {'F1': (f1, None, 60), 'F2': (f2, None, 10),
               'F3': (ratio(sum(close < pre for _, close, pre, _ in valid), len(valid)), sum(close < pre for _, close, pre, _ in valid), len(valid)),
               'F4': (ratio(sum(low_eligible), len(low_eligible)), sum(low_eligible), len(low_eligible)),
               'F5': (ratio(sum(x[0] for x in limited), len(limited)), sum(x[0] for x in limited), len(limited)),
               'F6': (ratio(sum(x[1] for x in limited), len(limited)), sum(x[1] for x in limited), len(limited))}
    result['factors'] = {key: {'rawValue': value, 'canonicalValue': canonical(value) if value is not None else None,
                                'numerator': numerator, 'denominator': denominator} for key, (value, numerator, denominator) in factors.items()}
    gates = {'daily': '0.98', 'history': '0.95', 'regime': '0.99', 'limit': '0.99'}
    for key, minimum in gates.items():
        if coverage[key] is None or coverage[key] < D(minimum):
            result['warnings'].append('INSUFFICIENT_' + key.upper() + '_COVERAGE')
    for board, counts in coverage['boards'].items():
        if counts['expected'] and counts['coverage'] < D('0.95'):
            result['warnings'].append('INSUFFICIENT_BOARD_' + board)
        # Do not let a large main board conceal concentrated missing inputs in
        # a small board; use the same frozen per-factor coverage thresholds.
        for field, denominator, minimum in (('historyReceived', counts['received'], METHOD['historyCoverage']),
                                             ('regimeReceived', counts['received'], METHOD['regimeCoverage']),
                                             ('limitReceived', counts['limitExpected'], METHOD['limitCoverage'])):
            if denominator and ratio(counts[field], denominator) < D(minimum):
                result['warnings'].append('INSUFFICIENT_BOARD_' + board + '_' + field)
    if any(f['rawValue'] is None for f in result['factors'].values()):
        result['warnings'].append('MISSING_FACTOR')
    if not result['warnings']:
        result['status'] = 'READY'
    return result


@fixed_precision
def calculate_score(raw, calendar, references):
    """references are immutable records with id/date/methodHash/status/factors.

    Reject duplicate dates: the caller must select one input vintage explicitly.
    Future references are ignored, and each factor must meet the same dated gate.
    """
    output = {**raw, 'score': None, 'scores': {}, 'dimensions': {}, 'referenceManifest': [], 'referenceCounts': {}}
    if raw['status'] != 'READY':
        return output
    target = raw['tradeDate']
    trading_window(calendar, target, 1)
    end = calendar.index(target)
    window = calendar[max(0, end-METHOD['referenceWindow']):end]
    rows = {}
    for row in references:
        if row['tradeDate'] not in window or row.get('methodHash') != METHOD_HASH or row.get('status') != 'READY':
            continue
        if row['tradeDate'] in rows:
            raise ValueError('AMBIGUOUS_REFERENCE_VERSION')
        rows[row['tradeDate']] = row
    output['status'] = 'WARMING_UP'
    for code in METHOD['factors']:
        qualified = [(day, row) for day, row in sorted(rows.items()) if row.get('factors', {}).get(code, {}).get('canonicalValue') is not None]
        output['referenceCounts'][code] = len(qualified)
        if len(qualified) < METHOD['minimumReferences']:
            return output
        interval = len(window) - window.index(qualified[0][0])
        if ratio(len(qualified), interval) < D(METHOD['referenceCoverage']):
            return output
        output['scores'][code] = percentile(raw['factors'][code]['canonicalValue'], [r['factors'][code]['canonicalValue'] for _, r in qualified])
        output['referenceManifest'].extend({'factor': code, 'id': row['id'], 'tradeDate': day} for day, row in qualified)
    output['dimensions'] = {name: sum(output['scores'][code] for code in codes) / 2 for name, codes in METHOD['dimensions'].items()}
    output['score'] = sum(output['dimensions'].values()) / 3
    output['contributions'] = {name: value / 3 for name, value in output['dimensions'].items()}
    # Reconcile the final Decimal division residue so displayed/audited parts sum
    # to the total; this changes only digits beyond the working precision.
    output['contributions']['selling'] = output['score'] - output['contributions']['intensity'] - output['contributions']['breadth']
    output['status'] = 'READY'
    return output


@fixed_precision
def adjacent_change(current, previous, calendar):
    """Only the actual previous trading date and matching source/method/universe."""
    empty = {'delta1d': None, 'changeContributions': None}
    if not previous or current.get('score') is None or previous.get('score') is None:
        return empty
    if current['tradeDate'] not in calendar or calendar.index(current['tradeDate']) == 0:
        return empty
    window = trading_window(calendar, current['tradeDate'], 2)
    if previous.get('tradeDate') != window[0]:
        return empty
    for key in ('methodHash', 'universeVersion', 'sourceVersion'):
        if not current.get(key) or current[key] != previous.get(key):
            return empty
    differences = {name: (number(current['dimensions'][name]) - number(previous['dimensions'][name])) / 3
                   for name in METHOD['dimensions']}
    delta = number(current['score']) - number(previous['score'])
    differences['selling'] = delta - differences['intensity'] - differences['breadth']
    return {'delta1d': delta, 'changeContributions': differences}
