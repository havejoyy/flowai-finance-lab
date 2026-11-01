"""Public evidence adapters for five-layer research. Stdlib only, TLS verified."""
import calendar
import hashlib
import io
import json
import math
import re
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timedelta
from urllib.parse import urlencode
from engine import CN,timestamp
from screening_data import public_bytes,number,parse_qfq_factors


def read(url,referer):
    raw=public_bytes(url,referer)
    return raw,dict(url=url,fetched_at=timestamp(),sha256=hashlib.sha256(raw).hexdigest())

def get(url,params,referer):
    raw,evidence=read(url+'?'+urlencode(params),referer)
    try:data=json.loads(raw)
    except (UnicodeDecodeError,ValueError):data=json.loads(raw.decode('gb18030'))
    return data,evidence

def month_calendar(month):
    d,e=get('https://www.szse.cn/api/report/exchange/onepersistenthour/monthList',{'month':month},'https://www.szse.cn/')
    rows=d.get('data');year,m=map(int,month.split('-'))
    expected={f'{month}-{i:02d}' for i in range(1,calendar.monthrange(year,m)[1]+1)}
    if not isinstance(rows,list) or {r.get('jyrq') for r in rows}!=expected or len(rows)!=len(expected) or any(str(r.get('jybz')) not in ('0','1') for r in rows):raise ValueError('交易所月日历范围或字段不完整')
    return dict(month=month,rows=[dict(date=r['jyrq'],is_open=str(r['jybz'])=='1') for r in rows],evidence=e)

def xlsx_rows(raw):
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if sum(i.file_size for i in z.infolist())>30_000_000:raise ValueError('证券清单解压后过大')
        shared=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            shared=[''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si',ns)]
        result=[]
        for row in ET.fromstring(z.read('xl/worksheets/sheet1.xml')).findall('.//s:row',ns):
            values={}
            for c in row:
                column=re.match('[A-Z]+',c.attrib['r']).group();kind=c.attrib.get('t');v=c.find('s:v',ns)
                text=''.join(c.find('s:is',ns).itertext()) if kind=='inlineStr' else (v.text if v is not None else '')
                if kind=='s':text=shared[int(text)]
                values[column]=text
            result.append(values)
    if not result:raise ValueError('证券清单为空')
    headers=result[0]
    return [{headers[k]:v for k,v in row.items() if k in headers} for row in result[1:]]

def security_registry():
    rows={};evidence=[]
    for typ in ('1','8'):
        params={'STOCK_TYPE':typ,'sqlId':'COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L','COMPANY_STATUS':'2,4,5,7,8','type':'inParams','isPagination':'true','pageHelp.pageSize':'10000','pageHelp.pageNo':'1'}
        d,e=get('https://query.sse.com.cn/sseQuery/commonQuery.do',params,'https://www.sse.com.cn/assortment/stock/list/share/')
        values=d.get('result');total=d.get('pageHelp',{}).get('total')
        if not isinstance(values,list) or not values or len(values)!=int(total):raise ValueError('上交所证券名录覆盖不足')
        evidence.append(e)
        for r in values:
            code=r['A_STOCK_CODE'];day=datetime.strptime(r['LIST_DATE'],'%Y%m%d').date().isoformat()
            if code in rows or not re.fullmatch(r'\d{6}',code):raise ValueError('证券名录重复或代码错误')
            rows[code]=dict(code=code,name=r['SEC_NAME_CN'],listing_date=day,market=1,board='科创板' if typ=='8' else '主板')
    raw,e=read('https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1110&TABKEY=tab1','https://www.szse.cn/');values=xlsx_rows(raw);evidence.append(e)
    if len(values)<1000:raise ValueError('深交所证券清单不完整')
    for r in values:
        code=r.get('A股代码','');day=r.get('A股上市日期','')
        if not re.fullmatch(r'\d{6}',code) or code in rows:raise ValueError('深交所代码无效或重复')
        datetime.strptime(day,'%Y-%m-%d');rows[code]=dict(code=code,name=r['A股简称'],listing_date=day,market=0,board=r['板块'])
    return dict(rows=rows,evidence=evidence,observed_at=timestamp(),scope='交易所当前沪深A股名录；不代表历史时点名录')

def benchmark(as_of):
    start=(datetime.fromisoformat(as_of)-timedelta(days=400)).strftime('%Y%m%d')
    d,e=get('https://q.stock.sohu.com/hisHq',dict(code='zs_000300',start=start,end=as_of.replace('-',''),stat=1,order='D',period='d',rt='json'),'https://q.stock.sohu.com/')
    if not isinstance(d,list) or len(d)!=1 or d[0].get('status')!=0:raise ValueError('沪深300响应异常')
    rows=[]
    for r in reversed(d[0]['hq']):rows.append(dict(date=r[0],open=number(r[1]),close=number(r[2]),low=number(r[5]),high=number(r[6])))
    validate_index(rows,as_of,120)
    return dict(rows=rows,evidence=e,basis='沪深300价格指数，搜狐公开历史')

def validate_index(rows,as_of,minimum=6):
    days=[r['date'] for r in rows]
    if len(rows)<minimum or days!=sorted(set(days)) or days[-1]!=as_of:raise ValueError('指数历史不足、日期错位或未到行情日')
    for r in rows:
        datetime.strptime(r['date'],'%Y-%m-%d')
        if any(r.get(k) is None or not math.isfinite(r[k]) or r[k]<=0 for k in ('open','close','high','low')) or not r['low']<=min(r['open'],r['close'])<=max(r['open'],r['close'])<=r['high']:raise ValueError('指数OHLC异常')

def sector_index(code,as_of):
    d,e=get('https://www.swsresearch.com/institute-sw/api/index_publish/trend/',dict(swindexcode=code,period='DAY'),'https://www.swsresearch.com/')
    if str(d.get('code'))!='200' or not isinstance(d.get('data'),list):raise ValueError('申万指数历史缺失')
    raw=[r for r in d['data'] if r.get('bargaindate','')<=as_of][-90:]
    if any(str(r.get('swindexcode'))!=code for r in raw):raise ValueError('行业指数代码不匹配')
    rows=[dict(date=r['bargaindate'],open=number(r['openindex']),close=number(r['closeindex']),high=number(r['maxindex']),low=number(r['minindex'])) for r in raw]
    validate_index(rows,as_of)
    return dict(rows=rows,evidence=e,basis='申万官方行业价格指数；不以当前成分回算历史')

POOL={'up':'getTopicZTPool','down':'getTopicDTPool','broken':'getTopicZBPool','previous':'getYesterdayZTPool'}
def limit_pool(kind,as_of):
    d,e=get('https://push2ex.eastmoney.com/'+POOL[kind],dict(ut='7eea3edcaed734bea9cbfc24409ed989',dpt='wz.ztzt',Pageindex=0,pagesize=10000,sort='zs:desc' if kind=='previous' else 'fbt:asc' if kind in ('up','broken') else 'fund:asc',date=as_of.replace('-','')),'https://quote.eastmoney.com/')
    data=d.get('data')
    if d.get('rc')!=0 or not isinstance(data,dict) or str(data.get('qdate'))!=as_of.replace('-','') or not isinstance(data.get('pool'),list) or data.get('tc')!=len(data['pool']):raise ValueError('股池日期或完整性核验失败；空响应不作零样本')
    rows={}
    for r in data['pool']:
        code=r.get('c')
        if not isinstance(code,str) or not re.fullmatch(r'\d{6}',code) or code in rows:raise ValueError('股池证券重复或无效')
        rows[code]=dict(code=code,name=r.get('n',''),pct=number(r.get('zdp')),consecutive=r.get('lbc'),raw=r)
    return dict(rows=rows,as_of=as_of,kind=kind,evidence=e,scope='东财公开股池，范围另与实际限价行情核对',membership='前一交易日收盘涨停集合' if kind=='previous' else '当日集合')

def tencent_limits(stocks,as_of,progress=lambda msg:None):
    symbols=[('sh' if r['market']==1 else 'sz')+r['code'] for r in stocks];values={};evidence=[];failures=[]
    def batch(group):
        try:
            raw,e=read('https://qt.gtimg.cn/q='+','.join(group),'https://gu.qq.com/');text=raw.decode('gb18030');result={}
            matches=list(re.finditer(r'v_((?:sh|sz)\d{6})="([^"\r\n]*)";',text))
            if {m.group(1) for m in matches}!=set(group):raise ValueError('限价行情批次覆盖不全')
            for m in matches:
                p=m.group(2).split('~');code=m.group(1)[2:]
                if len(p)<49 or p[2]!=code:raise ValueError('限价行情字段无效')
                at=datetime.strptime(p[30],'%Y%m%d%H%M%S').replace(tzinfo=CN)
                r=dict(code=code,at=at.isoformat(),date=at.date().isoformat(),close=number(p[3]),previous=number(p[4]),open=number(p[5]),high=number(p[33]),low=number(p[34]),upper=number(p[47]),lower=number(p[48]))
                result[code]=r
            return result,e,None
        except Exception as exc:return {},None,str(exc) if isinstance(exc,ValueError) else '限价行情获取失败'
    groups=[symbols[i:i+80] for i in range(0,len(symbols),80)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_map={pool.submit(batch,g):g for g in groups}
        for i,f in enumerate(as_completed(future_map)):
            result,e,error=f.result();values.update(result)
            if e:evidence.append(e)
            if error:failures.append(dict(codes=[s[2:] for s in future_map[f]],reason=error))
            progress('公开限价与日内区间 %d / %d 批次'%(i+1,len(groups)))
    if not values:raise ValueError('实际涨跌停价全部获取失败')
    return dict(rows=values,failures=failures,evidence=evidence,as_of=as_of,basis='腾讯当日实际涨跌停价格与OHLC；每条另核验交易日期与价格')

def augment_ohlc(history,stock,as_of):
    """Recover OHLC from already saved raw evidence before any new network request."""
    result=json.loads(json.dumps(history));ev=result.get('evidence',{});mapping={}
    if 'sohu' in ev and 'sina_qfq' in ev:
        factors=parse_qfq_factors(ev['sina_qfq']['raw'],('sh' if stock['market']==1 else 'sz')+stock['code'],as_of)
        for r in ev['sohu']['data'][0]['hq']:
            day=r[0];factor=next((v for d,v in factors if d<=day),None)
            if factor and day<=as_of:mapping[day]=dict(open=number(r[1]),high=number(r[6]),low=number(r[5]),raw_close=number(r[2]),qfq_factor=factor)
        for r in result['rows']:
            m=mapping.get(r['date'])
            if m and all(m.get(k) is not None for k in ('open','high','low')):r.update({k:m[k]/m['qfq_factor'] for k in ('open','high','low')});r.update(raw_open=m['open'],raw_high=m['high'],raw_low=m['low'])
    elif isinstance(ev.get('data'),dict) and isinstance(ev['data'].get('klines'),list):
        for line in ev['data']['klines']:
            p=line.split(',');mapping[p[0]]=dict(open=number(p[1]),high=number(p[3]),low=number(p[4]))
        for r in result['rows']:
            if r['date'] in mapping:r.update(mapping[r['date']])
    for r in result['rows']:
        if any(not isinstance(r.get(k),(int,float)) or isinstance(r[k],bool) or not math.isfinite(r[k]) or r[k]<=0 for k in ('open','high','low','close')) or not r['low']<=min(r['open'],r['close'])+1e-8<=max(r['open'],r['close'])+1e-8<=r['high']+2e-8:raise ValueError('历史原始证据无法还原一致OHLC；需重新取数')
    return result

RISK=re.compile('立案|退市|破产|重大风险|风险警示|终止上市|重整|重大违法')
def announcements(code,start,cutoff):
    params=dict(stock_list=code,sr=-1,page_size=100,page_index=1,ann_type='A',client_source='web',begin_time=start,end_time=datetime.fromisoformat(cutoff).astimezone(CN).date().isoformat());rows=[];evidence=[];expected=None
    for page in range(1,51):
        params['page_index']=page;d,e=get('https://np-anotice-stock.eastmoney.com/api/security/ann',params,'https://data.eastmoney.com/');data=d.get('data');evidence.append(e)
        if not isinstance(data,dict) or not isinstance(data.get('list'),list):raise ValueError('公告响应无效')
        total=data.get('total_hits')
        if not isinstance(total,int) or total<0 or (expected is not None and expected!=total):raise ValueError('公告分页总数变化')
        expected=total
        if len(data['list'])!=min(100,max(0,total-(page-1)*100)):raise ValueError('公告分页不完整')
        for r in data['list']:
            if code not in [str(c.get('stock_code')) for c in r.get('codes',[])]:raise ValueError('公告证券不匹配')
            text=r.get('display_time','');at=None
            try:at=datetime.strptime(text[:19],'%Y-%m-%d %H:%M:%S').replace(tzinfo=CN)
            except ValueError:pass
            title=r.get('title','');rows.append(dict(id=r.get('art_code'),title=title,published_at=at.isoformat() if at else None,date=r.get('notice_date','')[:10],risk_flag=bool(RISK.search(title)),url='https://data.eastmoney.com/notices/detail/'+code+'/'+str(r.get('art_code'))+'.html'))
        if page*100>=total:break
    else:raise ValueError('公告分页超限')
    if len({r['id'] for r in rows})!=len(rows):raise ValueError('公告分页存在重复')
    end=datetime.fromisoformat(cutoff);selected=[r for r in rows if not r['published_at'] or datetime.fromisoformat(r['published_at'])<=end]
    return dict(rows=selected,window_start=start,cutoff=cutoff,evidence=evidence,complete=True,scope='仅公告标题与类别窗口筛查，非全文风险审阅',time_unknown=any(not r['published_at'] for r in selected),risk_hits=[r for r in selected if r['risk_flag']])
