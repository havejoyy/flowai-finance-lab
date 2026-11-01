"""Five-layer close-of-day rule engine. Missing evidence is never a pass."""
import copy
import json
import math
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timedelta
from pathlib import Path
from engine import CN,digest,timestamp
import five_sources as src
from five_indicator_library import RuleBook,CATALOG,FIVE_INDICATORS
from data_recovery import error_info

ID='youzi-five-layer-breakout-v1'
TITLE='游资五层共振·放量突破'
DEFAULTS=dict(min_amount=2,min_pct=0,volume_ratio=1.5,prev_pct_max=3,sector_up_min=55,sector_top_pct=20,max_check=120)
NAMES=['市场环境','行业共振（主线代理）','行业内活跃代表','价格结构与信号证据','风险约束与规则纪律']
SPEC=Path(__file__).with_name('strategies')/'youzi-five-layer-breakout-v1.json'


def rule(key,name,value,operator,target,source='',reason='',status=None):
    if status is None:
        if value is None:status='待核验'
        else:
            good={'≥':lambda:value>=target,'≤':lambda:value<=target,'>':lambda:value>target,'=':lambda:value==target}[operator]()
            status='通过' if good else '不通过'
    return dict(id=key,name=name,value=value,operator=operator,target=target,status=status,passed=True if status in ('通过','不适用') else False if status=='不通过' else None,source=source,reason=reason)

def outcome(checks):
    if any(c['status']=='不通过' for c in checks):return '不通过'
    if any(c['status'] in ('待核验','未接入') for c in checks):return '待核验'
    return '通过'

def average(values):return sum(values)/len(values) if values else None

def pct_return(rows,n):return (rows[-1]['close']/rows[-n-1]['close']-1)*100

def rank(values,value):return 1+sum(x>value for x in values)

def month_range(first,last):
    y,m=first.year,first.month;result=[]
    while (y,m)<=(last.year,last.month):
        result.append('%04d-%02d'%(y,m));m+=1
        if m==13:y+=1;m=1
    return result

class Evidence:
    def __init__(self,manager,as_of,cutoff,overrides=None):
        self.m=manager;self.as_of=as_of;self.cutoff=cutoff;self.records=[];self.lock=threading.RLock();self.overrides=overrides or {}
        for k in ('resource_reused','resource_fetched'):self.m.stats.setdefault(k,0)
    def get(self,kind,key,fetch,ttl=86400,required=True,as_of=None):
        label=kind+':'+key;day=as_of or self.as_of
        try:
            cached=None if self.m.force else self.m.cached('five.'+kind,key,day,ttl)
            if cached:
                body=cached['body'];batch_id=cached['id'];mode='本地复用';counter='resource_reused'
            else:
                body=self.m.retry(self.overrides[label] if label in self.overrides else fetch,label)
                if not isinstance(body,dict) or 'rows' not in body:raise ValueError('公共资料格式无效')
                batch_id=self.m.publish('five.'+kind,key,day,body);mode='公开获取';counter='resource_fetched'
            with self.lock:
                self.records.append(dict(dataset=label,status='已校验',mode=mode,as_of=day,batch_id=batch_id,required=required,source=body.get('basis') or body.get('scope') or kind,evidence=body.get('evidence')))
                self.m.refs[label]=batch_id
            self.m.tick(counter,mode+' '+label)
            return body
        except Exception as exc:
            info=error_info(exc);error=info['message']+'；'+info['action']
            with self.lock:self.records.append(dict(dataset=label,status='待核验',mode='未替代数据',as_of=day,required=required,reason=error))
            self.m.issue(key,'five.'+kind,error);self.m.progress('待核验 '+label+'：'+error)
            return None
    def calendar(self):
        end=datetime.fromisoformat(self.cutoff).astimezone(CN).date();start=datetime.fromisoformat(self.as_of).date()-timedelta(days=380)
        values=[]
        def f(month):return self.get('calendar',month,lambda:src.month_calendar(month),as_of=month+'-01',ttl=3600 if month==end.strftime('%Y-%m') else 86400*30)
        with ThreadPoolExecutor(max_workers=2) as pool:
            for result in pool.map(f,month_range(start,end)):
                if result:values+=result['rows']
        days={r['date']:r['is_open'] for r in values};opens=sorted(d for d,o in days.items() if o and d<=self.as_of)
        expected=[];d=start
        while d<=end:expected.append(d.isoformat());d+=timedelta(days=1)
        latest=max((d for d,o in days.items() if o and d<=end.isoformat()),default='')
        valid=all(d in days for d in expected) and days.get(self.as_of) is True and latest==self.as_of and len(opens)>=120
        return opens,valid,dict(first=min(days,default=''),last=max(days,default=''),latest_open=latest,covered_days=len(days))


def run_five(snapshot,config,manager,get_history,progress,resources=None):
    book=RuleBook(config)
    as_of=snapshot['as_of'];cutoff=timestamp();e=Evidence(manager,as_of,cutoff,resources);p=config['params'];spec=json.loads(SPEC.read_text(encoding='utf-8'))
    opens,calendar_ok,calendar_meta=e.calendar()
    registry=e.get('registry','sse-szse',src.security_registry)
    benchmark=e.get('index','000300',lambda:src.benchmark(as_of),ttl=6*3600)
    metadata=(registry or {}).get('rows',{});all_quotes={r['code']:r for r in snapshot['rows']}
    def age(meta):return sum(d>=meta['listing_date'] for d in opens)
    eligible={c for c,m in metadata.items() if 'ST' not in m['name'].upper() and '退' not in m['name'] and m['listing_date']<=as_of and (not calendar_ok or age(m)>=120)}
    excluded=[];unavailable=[];valid=[]
    for r in snapshot['rows']:
        if 'ST' in r['name'].upper() or '退' in r['name'] or (r['code'] in metadata and r['code'] not in eligible):excluded.append(dict(code=r['code'],name=r['name'],reason='ST/退市标记或上市不足120交易日'));continue
        if not r.get('eod') or r.get('date')!=as_of or any(r.get(k) is None for k in ('close','pct','amount')) or (r.get('close') or 0)<=0 or (r.get('amount') or 0)<=0:unavailable.append(dict(code=r['code'],name=r['name'],reason='行情时点、价格或成交额待核验'));continue
        if r['code'] not in metadata:unavailable.append(dict(code=r['code'],name=r['name'],reason='交易所名录或上市日期待核验'));continue
        valid.append(r)
    meta_scope={r['code'] for r in snapshot['rows'] if 'ST' not in r['name'].upper() and '退' not in r['name']};registry_coverage=len(meta_scope&set(metadata))/len(meta_scope) if meta_scope else None
    known={r['code'] for r in valid};coverage=len(known & eligible)/len(eligible) if eligible else None
    # Quotes for actual limit prices cover the same eligible scope, not only finalists.
    actual=e.get('limit_prices','tencent',lambda:src.tencent_limits([r for r in snapshot['rows'] if r['code'] in eligible],as_of,progress),ttl=900)
    limits={};rates={}
    for code,r in (actual or {}).get('rows',{}).items():
        stock=all_quotes.get(code)
        if not stock or code not in known or r.get('date')!=as_of or r.get('at','')[11:16]<'15:00' or any(r.get(k) is None or r[k]<=0 for k in ('close','previous','high','low','upper','lower')) or abs(r['close']-stock['close'])>.011 or not r['low']<=r['close']<=r['high'] or r['upper']<=r['previous'] or r['lower']>=r['previous']:continue
        limits[code]=r
        expected_rate=.20 if metadata.get(code,{}).get('board') in ('创业板','科创板') else .10
        for rate in (expected_rate,):
            if abs(round(r['previous']*(1+rate)+1e-9,2)-r['upper'])<=.011 and abs(round(r['previous']*(1-rate)+1e-9,2)-r['lower'])<=.011:rates[code]=rate;break
    limit_coverage=len(limits)/len(valid) if valid else None
    ups={c for c,r in limits.items() if abs(r['close']-r['upper'])<.005}
    downs={c for c,r in limits.items() if abs(r['close']-r['lower'])<.005}
    broken={c for c,r in limits.items() if r['high']>=r['upper']-.005 and c not in ups}
    previous=e.get('pool','previous',lambda:src.limit_pool('previous',as_of),ttl=3600,required=book.enabled('M4'))
    # Dedicated pools are cross-check context. Actual daily upper/lower/high/close is
    # an equivalent direct measurement of the same set, not a fixed % shortcut.
    pools={k:e.get('pool',k,lambda k=k:src.limit_pool(k,as_of),ttl=3600,required=False) for k in ('up','down','broken')}
    prior={c for c in (previous or {}).get('rows',{}) if c in eligible};prior_observed=[all_quotes[c]['pct'] for c in prior if c in known]
    feedback=statistics.median(prior_observed) if prior_observed else None
    feedback_coverage=len(prior_observed)/len(prior) if prior else None
    market=dict(count=len(valid),up=sum(r['pct']>0 for r in valid),down=sum(r['pct']<0 for r in valid),flat=sum(r['pct']==0 for r in valid),amount=sum(r['amount'] for r in valid),mean_pct=average([r['pct'] for r in valid]),scope='交易所当前名录与同日有效沪深A股交集；数据覆盖率另列')
    groups={}
    for r in valid:
        if r.get('industry') not in ('行业未知','',None,'-'):groups.setdefault(r['industry'],[]).append(r)
    sectors=[dict(name=n,count=len(v),mean_pct=average([r['pct'] for r in v]),up_ratio=100*sum(r['pct']>0 for r in v)/len(v)) for n,v in groups.items()]
    sectors.sort(key=lambda s:(-s['mean_pct'],s['name']))
    for s in sectors:s.update(rank=rank([g['mean_pct'] for g in sectors],s['mean_pct']),rank_pct=100*rank([g['mean_pct'] for g in sectors],s['mean_pct'])/len(sectors))
    market_checks=[rule('G1','交易所日历覆盖且行情日为最近交易日',True if calendar_ok else None,'=',True,'calendar',reason=str(calendar_meta)),rule('G2','交易所证券名录可用',True if registry else None,'=',True,'registry'),rule('G3','合资格行情覆盖率（%）',coverage*100 if coverage is not None else None,'≥',98,'registry + snapshot',status='待核验' if coverage is None or coverage<.98 else None),rule('G4','实际限价行情覆盖率（%）',limit_coverage*100 if limit_coverage is not None else None,'≥',98,'腾讯同日实际涨跌停价',status='待核验' if limit_coverage is None or limit_coverage<.98 else None),rule('G5','沪深300历史可用',True if benchmark else None,'=',True,'index:000300')]
    valid_limits=limit_coverage is not None and limit_coverage>=.98
    breadth=100*market['up']/len(valid) if valid else None
    market_checks.append(rule('G2a','快照证券名录匹配率（%）',registry_coverage*100 if registry_coverage is not None else None,'≥',98,'证券名录与快照',status='待核验' if registry_coverage is None or registry_coverage<.98 else None))
    market_checks += [rule('M1','市场上涨占比（%）',breadth,'≥',40,'snapshot'),rule('M2','跌停占比（%）',100*len(downs)/len(valid) if valid and valid_limits else None,'≤',1,'同日实际跌停价与收盘价'),rule('M3','炸板率（%）',100*len(broken)/(len(ups)+len(broken)) if valid_limits and ups|broken else None,'≤',45,'同日实际涨停价、最高价与收盘价',status='不适用' if valid_limits and not ups|broken else None,reason='无触板样本' if valid_limits and not ups|broken else ''),rule('M4','昨日涨停T日涨幅中位数（%）',feedback if feedback_coverage is not None and feedback_coverage>=.98 else None,'≥',-1,'T日昨日涨停股池成员 + T日快照',status='不适用' if previous and not prior else None,reason='无合资格昨日涨停样本' if previous and not prior else '')]
    book.apply(market_checks)
    market_state=outcome(market_checks)
    checks_sources={};limits_info=[]
    for k,data in pools.items():
        if data:
            measured={'up':ups,'down':downs,'broken':broken}[k];external=set(data['rows']) & known
            checks_sources[k]=dict(measured=len(measured),public_pool=len(external),different_codes=sorted(measured^external),basis='实际限价与OHLC为主；专用股池作交叉核对')
    limits_info=['行业及名称为当前分类/标签；不能用于历史时点无偏回测。','五层量化代理不证明真实主线、龙头或游资资金来源。','个人计划、账户仓位与交易纪律无输入，标不适用。','公告仅按列明窗口的标题与类别扫描，非全文风险审阅。','全部适用硬规则通过才入选；缺失数据与未核验分别说明。']
    counts=dict(source=len(snapshot['rows']),market=len(valid),excluded=len(excluded),data_missing=len(unavailable),industry_covered=sum(len(v) for v in groups.values()),industry_missing=len(valid)-sum(len(v) for v in groups.values()),prefilter=0,checked=0,deferred=0,history_failed=0,passed=0,rejected=0,pre_rejected=0)
    layers=[dict(name=n,status='未进入',support=[],missing=[],refs=[],applicability='仅公开数据量化代理；不输出交易指令') for n in NAMES]
    layers[0].update(input_count=len(valid),passed_count=len(valid) if market_state=='通过' else 0 if market_state=='不通过' else None,status=market_state,support=['市场上涨占比 '+str(round(breadth,2) if breadth is not None else None)+'%；实际涨停 '+str(len(ups))+'，跌停 '+str(len(downs))+'，炸板未封 '+str(len(broken))+'。'],checks=market_checks,refs=['five_layer.market_checks'])
    report=dict(as_of=as_of,source=snapshot['source'],data_kind='real',market=market,sectors=sectors,industry_data=snapshot.get('industry_data'),counts=counts,evaluations=[],excluded=excluded,unavailable=unavailable,pre_rejected=[],deferred=[],failures={},histories={},layers=layers,limitations=limits_info,disclaimer='本工具只核验公开市场数据与研究规则，不构成个性化交易方案或收益承诺。')
    five=dict(indicator_catalog_hash=digest(FIVE_INDICATORS),selected_rules=book.rows,custom_selection='five_rules' in config,version=config['rule_version'],spec_hash=digest(spec),information_cutoff=cutoff,price_as_of=as_of,market_checks=market_checks,sector_evaluations=[],sources=e.records,calendar=calendar_meta,pool_cross_check=checks_sources,coverage=dict(quotes=coverage,limit_quotes=limit_coverage,prior_limit_samples=len(prior),prior_quote_coverage=feedback_coverage,eligible_expected=len(eligible),quote_valid=len(valid),missing_eligible=[dict(code=c,name=metadata[c]['name'],reason='未在行情目录中' if c not in all_quotes else '收盘报价无效') for c in sorted(eligible-known)]),stages=[],ranking_definition='仅通过集合内：行业相对表现30%、个股相对行业30%、成交额倍数20%、收盘位置20%；不是上涨概率',original_framework_mapping=['市场量化环境','行业联动代理，非题材确认','成交活跃代表，非因果龙头','客观价格结构；个人交易设想不适用','市场风险约束；个人纪律不适用'])
    report['five_layer']=five
    def finish(summary):
        report['summary']=summary;five['sources']=copy.deepcopy(e.records);five['stages']=[dict(name=l['name'],status=l['status'],input_count=l.get('input_count'),passed_count=l.get('passed_count'),pending_count=l.get('pending_count')) for l in layers];
        for i,stage in enumerate(five['stages'],1):
            stage['selected_count']=sum(CATALOG[x['id']]['layer']==i for x in book.rows)
            if stage['status']=='通过' and not stage['selected_count']:
                stage['status']='未设置筛选条件';layers[i-1]['status']='未设置筛选条件'
        five['required_data_missing']=any(s['required'] and s['status']=='待核验' for s in e.records) or any(c['status']=='待核验' for c in market_checks) or any(l['status']=='待核验' for l in layers) or counts['history_failed']>0 or any(x['status']=='待核验' for x in report['evaluations']);return report
    if market_state!='通过':
        layers[0].update(input_count=len(valid),passed_count=0 if market_state=='不通过' else None)
        for l in layers[1:]:l['missing']=['市场层'+market_state+'，未继续个股筛选，未自动放宽条件。']
        return finish('市场环境'+market_state+'；本轮0只全通过候选。已保留真实市场数据、五层状态及公开来源缺口。')
    progress('市场层通过，读取申万行业历史')
    class_cache=manager.cached('industry','sw-level1');class_data=class_cache['body'] if class_cache and snapshot.get('industry_data',{}).get('batch_id')==class_cache['id'] else None
    if not class_data:
        layers[1].update(status='待核验',missing=['行业分类未取得'])
        return finish('市场通过；行业分类待核验，未产生全通过候选。')
    index_data={}
    def fetch_index(code):return code,e.get('sector_index',code,lambda:src.sector_index(code,as_of),ttl=6*3600)
    with ThreadPoolExecutor(max_workers=2) as pool:
        for code,data in pool.map(fetch_index,list(class_data['catalog'])):
            if data:index_data[code]=data
    lookup={s['name']:s for s in sectors};members={};industry_to_code={}
    for code,g in class_data['groups'].items():
        industry_to_code[g['name']]=code;members[g['name']]={c for c,m in g['members'].items() if c in eligible and m['begin']<=as_of}
    complete=len(lookup)==len(class_data['catalog']) and len(index_data)==len(class_data['catalog'])
    selected={};bench_rows=(benchmark or {}).get('rows',[])
    for name,sector in lookup.items():
        ic=industry_to_code.get(name);idx=index_data.get(ic);expected=members.get(name,set());cov=sector['count']/len(expected) if expected else None
        aligned=bool(idx and benchmark and [r['date'] for r in idx['rows'][-6:]]==opens[-6:]==[r['date'] for r in bench_rows[-6:]])
        excess=pct_return(idx['rows'],5)-pct_return(bench_rows,5) if aligned else None
        positive=sum(idx['rows'][i]['close']>idx['rows'][i-1]['close'] for i in range(len(idx['rows'])-5,len(idx['rows']))) if aligned else None
        checks=[rule('SQ1','行业有效行情覆盖率（%）',cov*100 if cov is not None else None,'≥',95),rule('SQ2','行业有效成员数',sector['count'],'≥',10),rule('S1','行业当日强度名次',sector['rank'] if complete else None,'≤',max(1,math.ceil(len(class_data['catalog'])*p['sector_top_pct']/100))),rule('S2','行业上涨占比（%）',sector['up_ratio'],'≥',p['sector_up_min']),rule('S3','行业实际涨停家数',len(ups&expected),'≥',2),rule('S4','行业5日超额（百分点）',excess,'≥',1),rule('S5','行业近5日上涨天数',positive,'≥',3)]
        checks.append(rule('SQ3','行业与基准最近6期日期对齐',True if aligned else None,'=',True))
        book.apply(checks,{'S1':len(class_data['catalog'])})
        state=outcome(checks);five['sector_evaluations'].append(dict(name=name,code=ic,status=state,checks=checks,excess_5d=excess))
        if state=='通过':selected[name]=dict(sector=sector,index=idx,excess=excess)
    pending_sectors=sum(x['status']=='待核验' for x in five['sector_evaluations']);layers[1].update(status='通过' if selected else '待核验' if pending_sectors else '不通过',support=['通过行业 '+str(len(selected))+' 个。'],input_count=len(class_data['catalog']),passed_count=len(selected),pending_count=pending_sectors)
    pool=[]
    for stock in valid:
        if stock['industry'] not in selected:continue
        group=groups[stock['industry']];cap=max(1,math.ceil(len(group)*.20));pos=rank([r['amount'] for r in group],stock['amount'])
        move=stock['pct']/(rates[stock['code']]*100) if stock['code'] in rates else None
        pre=book.apply([rule('C1','当日成交额（亿元）',stock['amount']/1e8,'≥',p['min_amount']),rule('C2','行业成交额名次',pos,'≤',cap),rule('C5','标准化当日涨幅',move,'≥',.30)],{'C2':len(group)})
        if outcome(pre)=='不通过':report['pre_rejected'].append(dict(code=stock['code'],name=stock['name'],reason='成交额、行业活跃度或标准化涨幅未达门槛'));continue
        pool.append((stock,pos,move))
    pool.sort(key=lambda x:(-x[0]['amount'],x[0]['code']));checked=pool[:p['max_check']];report['deferred']=[dict(code=s['code'],name=s['name']) for s,_,_ in pool[p['max_check']:]]
    counts.update(prefilter=len(pool),checked=len(checked),deferred=len(report['deferred']),pre_rejected=len(report['pre_rejected']))
    if not checked:
        layers[2].update(status='待核验' if pending_sectors else '不通过',input_count=len(pool),passed_count=0,missing=['无通过的行业/个股预筛，后两层未进入。'])
        return finish('完成市场与行业核验；无可进入个股历史核验的候选，0只全通过。')
    from screening import metrics
    def evaluate(entry):
        stock,pos,move=entry;code=stock['code'];checks=[];layer_status={};h=None
        try:
            h=src.augment_ohlc(get_history(stock,as_of),stock,as_of)
            days=[r['date'] for r in h['rows']];aligned=days[-61:]==opens[-61:] and len(days)>=61
            if not aligned:raise ValueError('个股近61交易日不连续或交易日历不一致；不删除缺行后凑窗口')
            m=metrics(h);r=h['rows'];industry=selected[stock['industry']];extra=pct_return(r,5)-pct_return(industry['index']['rows'],5)
            checks=[rule('C1','当日成交额（亿元）',stock['amount']/1e8,'≥',p['min_amount']),rule('C2','行业成交额名次',pos,'≤',max(1,math.ceil(len(groups[stock['industry']])*.20))),rule('C3','个股相对行业5日表现（百分点）',extra,'≥',2),rule('C4a','收盘价 / MA20',m['close']/m['ma20'],'>',1),rule('C4b','MA20 / MA60',m['ma20']/m['ma60'],'>',1),rule('C5','标准化当日涨幅',move,'≥',.30)]
            book.apply(checks,{'C2':len(groups[stock['industry']])})
            layer_status['L3']=outcome(checks);past_high=max(x['high'] for x in r[-21:-1]);spread=r[-1]['high']-r[-1]['low'];location=(m['close']-r[-1]['low'])/spread if spread>0 else None
            price=[rule('P1','收盘价 / 前20日最高价',m['close']/past_high,'>',1),rule('P2','前五日成交额倍数',m['volume_ratio'],'≥',p['volume_ratio']),rule('P3','收盘位置',location,'≥',.70,status='不通过' if spread<=0 else None)]
            book.apply(price)
            checks+=price;layer_status['L4']=outcome(price) if layer_status['L3']=='通过' else '未进入'
            tr=[max(r[i]['high']-r[i]['low'],abs(r[i]['high']-r[i-1]['close']),abs(r[i]['low']-r[i-1]['close'])) for i in range(len(r)-14,len(r))];rate=rates.get(code)
            normalized=(average(tr)/m['close'])/rate if rate else None
            risk=[rule('R1','上市交易日数（本次日历覆盖内下界）',age(metadata[code]),'≥',120),rule('R2','20日平均成交额（亿元）',average([x['amount'] for x in r[-20:]])/1e8,'≥',1),rule('R3','标准化ATR14',normalized,'≤',.50),rule('R4','MA20偏离（%）',(m['close']/m['ma20']-1)*100,'≤',12),rule('R5','非一字有效价格区间',spread>0,'=',True)]
            book.apply(risk)
            notice=None
            if book.enabled('R6') and layer_status['L3']==layer_status['L4']=='通过' and outcome(risk)=='通过':
                notice=e.get('announcements',code,lambda:src.announcements(code,opens[-5],cutoff),as_of=cutoff,ttl=300)
                ok=None if notice is None or notice['time_unknown'] or notice['risk_hits'] else True
                risk.append(rule('R6','公告窗口标题扫描完整且无待复核命中',ok,'=',True,'公开公告目录',reason='标题/时间待核验；不作无风险判断' if ok is None else '限定窗口和标题规则，非全文审阅'))
            else:risk.append(rule('R6','公告标题扫描',None,'=',True,status='未进入' if book.enabled('R6') else '不适用',reason='前置规则未通过或未选择，未额外请求公告'))
            book.apply(risk)
            from screening_indicators import indicator_checks
            risk+=indicator_checks(config,h)
            checks+=risk;layer_status['L5']=outcome(risk) if layer_status['L4']=='通过' else '未进入'
            done=all(layer_status.get(x)=='通过' for x in ('L3','L4','L5'))
            state='符合条件' if done else '未满足条件' if any(x['status']=='不通过' for x in checks) else '待核验'
            return dict(stock=stock,status=state,checks=checks,metrics=m,reason='',basis=h['basis'],five_status=layer_status,announcement=notice,rank_metrics=[industry['excess'],extra,m['volume_ratio'],location]),h,None
        except Exception as exc:
            reason=str(exc) if isinstance(exc,ValueError) else '个股五层核验失败（'+type(exc).__name__+'）'
            return dict(stock=stock,status='待核验',checks=checks,metrics=None,reason=reason,five_status=dict(L3='待核验',L4='未进入',L5='未进入')),h,reason
    with ThreadPoolExecutor(max_workers=2) as workers:
        for i,(ev,h,error) in enumerate(workers.map(evaluate,checked)):
            report['evaluations'].append(ev)
            if h:report['histories'][ev['stock']['code']]=h
            if error:report['failures'][ev['stock']['code']]=error
            progress('五层个股核验 %d / %d'%(i+1,len(checked)))
    passed=[x for x in report['evaluations'] if x['status']=='符合条件'];n=len(passed)
    if n>1:
        for x in passed:
            score=0
            for i,w in enumerate((30,30,20,20)):
                v=x['rank_metrics'][i];values=[z['rank_metrics'][i] for z in passed];score+=w*(sum(z<v for z in values)+.5*(sum(z==v for z in values)-1))/(n-1)
            x['research_rank_score']=score
    for index,key in enumerate(('L3','L4','L5'),2):
        relevant=[x for x in report['evaluations'] if x['five_status'].get(key)!='未进入'];ps=sum(x['five_status'].get(key)=='通过' for x in relevant);unknown=sum(x['five_status'].get(key)=='待核验' for x in relevant)
        layers[index].update(status='通过' if ps else '待核验' if unknown else '不通过' if relevant else '未进入',input_count=len(relevant),passed_count=ps,pending_count=unknown,support=['本层通过 '+str(ps)+' 只，待核验 '+str(unknown)+' 只。'])
    report['evaluations'].sort(key=lambda x:({'符合条件':0,'待核验':1,'未满足条件':2}[x['status']],-x.get('research_rank_score',0),-x['stock']['amount'],x['stock']['code']))
    counts.update(passed=len(passed),rejected=sum(x['status']=='未满足条件' for x in report['evaluations']),history_failed=len(report['failures']))
    return finish('游资五层核验完成：初筛%d只，核验%d只，%d只全部通过，%d只待核验，%d只未核验。'%(len(pool),len(checked),len(passed),sum(x['status']=='待核验' for x in report['evaluations']),counts['deferred']))
