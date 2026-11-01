"""Public end-of-day quotes. Standard library, no credentials, no model calls."""
import json
import math
import os
import re
import time
from threading import Lock
from datetime import datetime, timedelta
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import URLError,HTTPError
from http.client import RemoteDisconnected
from engine import CN, timestamp, digest
from providers import NoRedirect

QUOTE_URL = 'https://push2.eastmoney.com/api/qt/clist/get'
HISTORY_URL = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
FIELDS = 'f2,f3,f6,f8,f12,f13,f14,f20,f100,f124'
UNIVERSE = 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23'
SINA_API = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.'
SINA_QUOTES = 'https://hq.sinajs.cn/list='
HISTORY_FALLBACK_LOCK = Lock()


class SourceUnavailable(ValueError):
    """Transport failure; schema/coverage failures must not silently switch sources."""


def public_bytes(url, referer):
    # Scope this setting to public quotes. Never alter global proxy/model settings.
    mode = os.environ.get('JOYWORKFLOW_MARKET_PROXY', 'direct')
    if mode not in ('direct', 'environment'):
        raise ValueError('JOYWORKFLOW_MARKET_PROXY 只支持 direct 或 environment')
    request = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': referer})
    handlers = [NoRedirect()]
    if mode == 'direct':
        handlers.append(ProxyHandler({}))
    for attempt in range(2):
        try:
            with build_opener(*handlers).open(request, timeout=15) as response:
                raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise ValueError('公开行情响应过大')
            return raw
        except (URLError, RemoteDisconnected, TimeoutError, ConnectionError) as exc:
            from data_recovery import error_info
            if not error_info(exc)['retryable']:raise
            if attempt == 0:
                time.sleep(.5)
                continue
            kind = type(exc).__name__ + (' %s' % exc.code if hasattr(exc,'code') else '')
            raise SourceUnavailable('公开行情连接失败（%s）；未使用示例或旧缓存' % kind) from None


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def get_json(url, params):
    full = url + '?' + urlencode(params)
    result = json.loads(public_bytes(full, 'https://quote.eastmoney.com/'))
    if not isinstance(result, dict) or result.get('rc') != 0 or not isinstance(result.get('data'), dict):
        raise ValueError('公开接口没有返回完整数据')
    return result['data']


def fetch_eastmoney_snapshot(progress=lambda message: None, getter=get_json, now=None):
    now = now or datetime.now(CN)
    rows, pages, seen, expected = [], [], set(), None
    for page in range(1, 150):
        params = dict(pn=page, pz=100, po=0, np=1, fltt=2, invt=2, fid='f12', fs=UNIVERSE, fields=FIELDS)
        data = getter(QUOTE_URL, params)
        total = data.get('total')
        if not isinstance(total, int) or not 1 <= total <= 15000:
            raise ValueError('证券列表总数无效')
        if expected is not None and total != expected:
            raise ValueError('分页期间证券范围变化，请重新运行')
        expected = total
        diff = data.get('diff')
        if not isinstance(diff, list) or not diff:
            raise ValueError('证券分页不完整，停止筛选')
        pages.append({'url': QUOTE_URL, 'params': params, 'fetched_at': timestamp(), 'data': data})
        for item in diff:
            code = str(item.get('f12', ''))
            market = item.get('f13')
            if not re.fullmatch(r'\d{6}', code) or market not in (0, 1) or code in seen:
                raise ValueError('证券列表重复或代码无效，停止筛选')
            seen.add(code)
            epoch = number(item.get('f124'))
            try:
                at = datetime.fromtimestamp(epoch, CN) if epoch and epoch > 0 else None
            except (ValueError, OverflowError, OSError):
                at = None
            if at and at > now + timedelta(minutes=5):
                raise ValueError('行情时间在本机时间之后，请核对系统时钟或数据源')
            rows.append(dict(code=code, market=market, name=str(item.get('f14', code)),
                             industry=str(item.get('f100') or '行业未知'),
                             close=number(item.get('f2')), pct=number(item.get('f3')),
                             amount=number(item.get('f6')), turnover=number(item.get('f8')),
                             cap=number(item.get('f20')), quote_at=at.isoformat() if at else '',
                             date=at.date().isoformat() if at else ''))
        progress('已取得行情 %d / %d' % (len(rows), expected))
        if len(rows) >= expected:
            break
    if len(rows) != expected:
        raise ValueError('证券分页覆盖不足，停止筛选')
    as_of = validate_snapshot_dates(rows, now)
    return dict(as_of=as_of, fetched_at=timestamp(), source='东方财富公开行情',
                scope='沪深A股，来源当前证券范围；不含北交所', data_kind='real', rows=rows,
                evidence={'pages': pages}, snapshot_hash=digest(pages))


def validate_snapshot_dates(rows, now):
    dates = [r['date'] for r in rows if r['date']]
    if not dates:
        raise ValueError('行情缺少日期')
    as_of = max(dates)
    if as_of == now.date().isoformat() and (now.hour, now.minute) < (15, 15):
        raise ValueError('当前为盘中或收盘整理时段；请15:15之后运行收盘筛选，历史报告仍可查看')
    if (now.date() - datetime.fromisoformat(as_of).date()).days > 7:
        raise ValueError('来源快照超过7天，未当作当前行情使用')
    # A quote with today's date but a pre-close timestamp is not verified EOD evidence.
    for row in rows:
        at = datetime.fromisoformat(row['quote_at']) if row['quote_at'] else None
        if at and at > now + timedelta(minutes=5):
            raise ValueError('行情时间在本机时间之后，请核对系统时钟或数据源')
        row['eod'] = bool(at and row['date'] == as_of and (at.hour, at.minute) >= (15, 0))
    return as_of


def sina_catalog(read, progress=lambda message: None):
    """Fresh directory only; undated list prices are never used as quotes."""
    count_url = SINA_API + 'getHQNodeStockCount?node=hs_a'
    def count():
        value = json.loads(read(count_url))
        if not re.fullmatch(r'\d+', str(value)) or not 1 <= int(value) <= 15000:
            raise ValueError('备用证券目录总数无效')
        return int(value)
    total = count()
    catalog = {}
    for page in range(1, math.ceil(total / 80) + 1):
        url = SINA_API + 'getHQNodeData?' + urlencode(dict(page=page, num=80, sort='symbol', asc=1, node='hs_a'))
        data = json.loads(read(url))
        expected = min(80, total - (page - 1) * 80)
        if not isinstance(data, list) or len(data) != expected:
            raise ValueError('备用证券目录分页不足')
        for item in data:
            symbol = item.get('symbol', '')
            if not re.fullmatch(r'(sh|sz|bj)\d{6}', symbol) or symbol in catalog:
                raise ValueError('备用证券目录代码重复或无效')
            catalog[symbol] = str(item.get('name') or symbol)
        progress('备用证券目录 %d / %d' % (len(catalog), total))
    if count() != total:
        raise ValueError('备用证券目录在获取期间变化，请重试')
    return {s: n for s, n in catalog.items() if re.fullmatch(r'(sh(600|601|603|605|688|689)|sz(000|001|002|003|300|301))\d{3}', s)}


def parse_sina_quotes(raw, catalog):
    matches = list(re.finditer(r'var hq_str_((?:sh|sz)\d{6})="([^"\r\n]*)";', raw))
    if re.sub(r'var hq_str_((?:sh|sz)\d{6})="([^"\r\n]*)";', '', raw).strip():
        raise ValueError('备用行情格式异常')
    rows, seen = [], set()
    for match in matches:
        symbol, body = match.groups()
        if symbol not in catalog or symbol in seen:
            raise ValueError('备用行情证券不匹配或重复')
        seen.add(symbol)
        parts = body.split(',')
        # Empty explicitly returned quotes remain in coverage as unavailable.
        row = dict(code=symbol[2:], market=1 if symbol.startswith('sh') else 0,
                   name=catalog[symbol], industry='行业未知', close=None, pct=None,
                   amount=None, turnover=None, cap=None, quote_at='', date='')
        if body:
            if len(parts) < 32:
                raise ValueError('备用行情字段不足')
            at = datetime.strptime(parts[30] + ' ' + parts[31], '%Y-%m-%d %H:%M:%S').replace(tzinfo=CN)
            close, previous = number(parts[3]), number(parts[2])
            row.update(name=parts[0] or row['name'], close=close,
                       pct=(close / previous - 1) * 100 if close is not None and previous and previous > 0 else None,
                       amount=number(parts[9]), quote_at=at.isoformat(), date=at.date().isoformat())
        rows.append(row)
    if seen != set(catalog):
        raise ValueError('备用行情批次覆盖不足')
    return rows


def fetch_sina_snapshot(progress=lambda message: None, now=None, reader=None):
    now = now or datetime.now(CN)
    evidence = []
    def read(url):
        raw = (reader or (lambda u: public_bytes(u, 'https://finance.sina.com.cn/')))(url)
        text = raw.decode('gb18030')
        evidence.append(dict(url=url, fetched_at=timestamp(), raw=text))
        return text
    progress('东方财富列表不可用，正在实时获取新浪备用目录')
    catalog = sina_catalog(read, progress)
    if not catalog:
        raise ValueError('备用目录没有沪深A股')
    rows, symbols = [], sorted(catalog)
    for start in range(0, len(symbols), 80):
        batch = symbols[start:start+80]
        rows.extend(parse_sina_quotes(read(SINA_QUOTES + ','.join(batch)), {s: catalog[s] for s in batch}))
        progress('新浪同日行情 %d / %d' % (len(rows), len(catalog)))
    as_of = validate_snapshot_dates(rows, now)
    return dict(as_of=as_of, fetched_at=timestamp(), source='新浪公开行情（备用）；日K来源见候选证据',
                scope='新浪实时目录内沪深A股；不含北交所，未与交易所名录核对', data_kind='real', rows=rows,
                warnings=['备用来源未提供行业分类；板块共振无法核验，未用旧分类填充。'],
                evidence={'requests': evidence}, snapshot_hash=digest(evidence))


def fetch_snapshot(progress=lambda message: None, getter=get_json, now=None):
    try:
        snapshot = fetch_eastmoney_snapshot(progress, getter, now)
    except (SourceUnavailable,HTTPError) as exc:
        # Never follow an untrusted redirect. A different public provider is
        # independent, and its entire response still passes the normal checks.
        if isinstance(exc,HTTPError) and exc.code not in (301,302,303,307,308):raise
        snapshot = fetch_sina_snapshot(progress, now)
        snapshot['fallback_reason'] = str(exc)
    snapshot['adapter_version'] = 'public-market-v2'
    snapshot['network_mode'] = os.environ.get('JOYWORKFLOW_MARKET_PROXY', 'direct')
    return snapshot


def parse_history(data, code, as_of, min_rows=60):
    if str(data.get('code')) != code:
        raise ValueError('历史行情代码不匹配')
    raw = data.get('klines')
    if not isinstance(raw, list):
        raise ValueError('日K缺失')
    rows = []
    for line in raw:
        parts = line.split(',')
        if len(parts) < 11:
            raise ValueError('日K字段不完整')
        day = datetime.strptime(parts[0], '%Y-%m-%d').date().isoformat()
        if day > as_of:
            raise ValueError('历史行情超出研究截止日')
        row = dict(date=day, close=number(parts[2]), amount=number(parts[6]), pct=number(parts[8]))
        if row['close'] is None or row['close'] <= 0 or row['amount'] is None or row['amount'] <= 0 or row['pct'] is None:
            raise ValueError('日K缺失价格、成交额或涨幅；停牌及无成交记录不补零')
        rows.append(row)
    dates = [r['date'] for r in rows]
    if dates != sorted(set(dates)) or not dates or dates[-1] != as_of:
        raise ValueError('日K日期重复、顺序错误或未更新至快照日')
    if len(rows) < min_rows:
        raise ValueError('不足60个有效日K观测，无法核验趋势')
    return rows


def fetch_history(stock, as_of, getter=get_json, start=None):
    params = dict(secid='%d.%s' % (stock['market'], stock['code']), klt=101, fqt=1,
                  end=as_of.replace('-', ''), lmt=90, fields1='f1,f2,f3,f4,f5,f6',
                  fields2='f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61')
    if start:
        params['beg'] = start.replace('-', '')
    try:
        data = getter(HISTORY_URL, params)
    except (SourceUnavailable,HTTPError) as exc:
        if isinstance(exc,HTTPError) and exc.code not in (301,302,303,307,308):raise
        history = fetch_secondary_history(stock, as_of, start=start)
        history['fallback_reason'] = str(exc)
        return history
    return {'rows': parse_history(data, stock['code'], as_of, min_rows=1 if start else 60),
            'evidence': {'url': HISTORY_URL, 'params': params, 'fetched_at': timestamp(), 'data': data},
            'basis': '来源前复权日K；均线和收益按有效观测日，不视为完整交易日历'}


def parse_qfq_factors(raw, symbol, as_of):
    prefix = 'var ' + symbol + 'qfq='
    if not raw.startswith(prefix):
        raise ValueError('复权因子证券标识不匹配')
    data, _ = json.JSONDecoder().raw_decode(raw[len(prefix):].lstrip())
    factors = data.get('data') if isinstance(data,dict) else None
    if not isinstance(factors,list) or not factors or data.get('total') != len(factors):
        raise ValueError('复权因子缺失或不完整')
    parsed = []
    for item in factors:
        day = datetime.strptime(item['d'],'%Y-%m-%d').date().isoformat()
        value = number(item.get('f'))
        if value is None or value <= 0 or day > as_of:
            raise ValueError('复权因子无效或包含截止日之后的事件')
        parsed.append((day,value))
    days = [d for d,v in parsed]
    if days != sorted(set(days),reverse=True) or abs(parsed[0][1]-1) > 1e-8:
        raise ValueError('复权因子日期或当前基准无效')
    return parsed


def parse_secondary_history(sohu, factors, as_of, min_rows=60):
    if not isinstance(sohu,list) or len(sohu)!=1 or sohu[0].get('status')!=0:
        raise ValueError('备用历史响应异常')
    raw = sohu[0].get('hq')
    if not isinstance(raw,list) or not raw or any(not isinstance(r,list) or len(r)<9 for r in raw):
        raise ValueError('备用历史字段不足')
    dates = [datetime.strptime(r[0],'%Y-%m-%d').date().isoformat() for r in raw]
    if len(raw)<min_rows or dates!=sorted(set(dates),reverse=True) or dates[0]!=as_of or any(d>as_of for d in dates):
        raise ValueError('备用历史不足60期、日期错误或未到快照日')
    rows = []
    for r in reversed(raw[:90]):
        day = r[0]
        factor = next((v for d,v in factors if d<=day),None)
        close,amount,pct = number(r[2]),number(r[8]),number(str(r[4]).rstrip('%'))
        if factor is None or any(v is None or v<=0 for v in (close,amount)) or pct is None:
            raise ValueError('备用历史价格、成交额或复权因子缺失')
        rows.append(dict(date=day,close=close/factor,amount=amount*10000,pct=pct,
                         raw_close=close,qfq_factor=factor))
    return rows


def fetch_secondary_history(stock, as_of, start=None):
    # Public fallback services are less tolerant of parallel requests.
    ranged = bool(start)
    with HISTORY_FALLBACK_LOCK:
        symbol = ('sh' if stock['market']==1 else 'sz') + stock['code']
        start = start.replace('-', '') if start else (datetime.fromisoformat(as_of)-timedelta(days=250)).strftime('%Y%m%d')
        params = dict(code='cn_'+stock['code'],start=start,end=as_of.replace('-',''),
                      stat=1,order='D',period='d',callback='historySearchHandler',rt='json')
        url = 'https://q.stock.sohu.com/hisHq?' + urlencode(params)
        factor_url = 'https://finance.sina.com.cn/realstock/company/'+symbol+'/qfq.js'
        sohu = json.loads(public_bytes(url,'https://q.stock.sohu.com/').decode('gb18030'))
        raw_factors = public_bytes(factor_url,'https://finance.sina.com.cn/').decode('gb18030')
        factors = parse_qfq_factors(raw_factors,symbol,as_of)
        return dict(rows=parse_secondary_history(sohu,factors,as_of,min_rows=1 if ranged else 60),
                    evidence={'sohu':dict(url=url,data=sohu,fetched_at=timestamp()),
                              'sina_qfq':dict(url=factor_url,raw=raw_factors,fetched_at=timestamp())},
                    basis='搜狐收盘价 ÷ 新浪当日适用因子（乘法前复权）；搜狐成交额万元转元与原始涨幅。复权口径可能与东方财富有差异，来源已留档')


def demo_snapshot():
    # Fictional full input, calculated by the same rules; not a preset model response.
    as_of = '2026-01-30'
    rows = []
    for i in range(12):
        rows.append(dict(code='DEMO%02d' % i, market=1, name='示例标的 %s' % chr(65+i),
                         industry='示例行业甲' if i < 8 else '示例行业乙', close=10+i,
                         pct=6.0 if i % 3 == 0 else (2.0 if i < 8 else -2.0), amount=(12-i)*1e8,
                         turnover=5, cap=100e8, quote_at=as_of+'T15:00:00+08:00', date=as_of, eod=True))
    return dict(as_of=as_of, fetched_at=timestamp(), source='程序生成的虚构教学数据',
                scope='12个虚构标的，不对应真实证券', data_kind='synthetic', rows=rows,
                evidence={'generator':'demo-v1'}, snapshot_hash=digest(rows))


def demo_history(stock, as_of):
    end = datetime.fromisoformat(as_of)
    dates = []
    while len(dates) < 90:
        if end.weekday() < 5:
            dates.append(end.date().isoformat())
        end -= timedelta(days=1)
    closes = [stock['close']*(.7+.3*i/89) for i in range(90)]
    closes[-1] = stock['close']; closes[-2] = closes[-1]/(1+stock['pct']/100)
    closes[-3] = closes[-2]/1.01
    rows = [dict(date=d, close=closes[i], amount=stock['amount']/2.5,
                 pct=100*(closes[i]/closes[i-1]-1) if i else 0) for i,d in enumerate(reversed(dates))]
    rows[-1]['amount'] = stock['amount']
    return dict(rows=rows, evidence={'generator':'demo-v1','rows':rows}, basis='虚构日K，仅供流程演示')


def repair_snapshot_gaps(snapshot,progress=lambda text:None,reader=None,now=None):
    """One targeted public recheck for up to 80 invalid existing quotes; same T only."""
    import copy
    result=copy.deepcopy(snapshot);now=now or datetime.now(CN)
    def invalid(r):return not r.get('eod') or r.get('date')!=snapshot['as_of'] or any(r.get(k) is None for k in ('close','pct','amount')) or (r.get('close') or 0)<=0 or (r.get('amount') or 0)<=0
    gaps=[r for r in result['rows'] if 'ST' not in r['name'].upper() and '退' not in r['name'] and invalid(r)]
    if not gaps:return result
    targets=gaps[:80];catalog={('sh' if r['market']==1 else 'sz')+r['code']:r['name'] for r in targets}
    url=SINA_QUOTES+','.join(catalog);progress('自动补取缺失报价 %d 只（仅核验同一行情日）'%len(targets))
    record=dict(attempted=len(targets),recovered=0,remaining=len(gaps),as_of=snapshot['as_of'],url=url,fetched_at=timestamp())
    try:
        raw=(reader or (lambda u:public_bytes(u,'https://finance.sina.com.cn/')))(url)
        text=raw.decode('gb18030');fresh=parse_sina_quotes(text,catalog)
        # Empty responses remain explicit gaps; never infer a date from the local clock.
        if any(r.get('date') for r in fresh):validate_snapshot_dates(fresh,now)
        observed={r['code']:r for r in fresh};record['raw']=text
        for row in result['rows']:
            replacement=observed.get(row['code'])
            if replacement and not invalid(replacement):
                for key in ('close','pct','amount','quote_at','date','eod'):row[key]=replacement[key]
                record['recovered']+=1
        record['remaining']=len(gaps)-record['recovered']
    except Exception as exc:
        from data_recovery import error_info
        record['error']=error_info(exc)
    result.setdefault('evidence',{})['quote_gap_recheck']=record
    result['snapshot_hash']=digest(result['evidence'])
    progress('缺失报价补取完成：恢复 %d 只，仍缺 %d 只，未以旧数据替代'%(record['recovered'],record['remaining']))
    return result
