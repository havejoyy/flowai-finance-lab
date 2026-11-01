"""Deterministic screening and five-layer evidence reports; no LLM invocation."""
import json
import math
import re
import uuid
import copy
import threading
from datetime import datetime
from market_store import MarketStore, validate_snapshot, validate_history
from data_recovery import error_info
from engine import CN
from concurrent.futures import ThreadPoolExecutor, as_completed
from engine import encode, digest, timestamp, VERSION
from skill_runner import resources, LAYERS, DISCLAIMER
from screening_data import fetch_snapshot, fetch_history, demo_snapshot, demo_history

LIMITS = {'min_amount': (0.1, 100, .5), 'min_pct': (-10, 30, 1), 'volume_ratio': (.5, 10, 2),
          'prev_pct_max': (-10, 10, 3), 'sector_up_min': (0, 100, 60),
          'sector_top_pct': (1, 100, 35), 'max_check': (10, 120, 40)}
from screening_indicators import validate_indicators, indicator_checks
from five_indicator_library import validate_rules
from five_layer import ID as FIVE_ID, TITLE as FIVE_TITLE, DEFAULTS as FIVE_DEFAULTS

PRESETS = [
 {'id':'volume','name':'放量走强','description':'成交额高于前五日均值，观察价格与均线是否同向。','version':'1',
  'params':dict((k,v[2]) for k,v in LIMITS.items())},
 {'id':'reversal','name':'弱转强观察','description':'前一日较弱、当日走强，同时核验成交与趋势。','version':'1',
  'params':dict({k:v[2] for k,v in LIMITS.items()},min_pct=5,volume_ratio=1.2)},
 {'id':'sector','name':'板块共振','description':'观察行业上涨范围及相对强弱，再核验其中的候选。','version':'1',
  'params':dict({k:v[2] for k,v in LIMITS.items()},min_pct=0,volume_ratio=1)},
 {'id':FIVE_ID,'name':FIVE_TITLE,'description':'五层核验市场、行业、活跃代表、放量突破与风险约束；必需数据缺失则待核验。','version':'1','live_only':True,'params':dict(FIVE_DEFAULTS)},
]


def strategy(data):
    if not isinstance(data, dict) or data.get('template') not in [p['id'] for p in PRESETS]:
        raise ValueError('请选择有效策略模板')
    base = next(p for p in PRESETS if p['id'] == data['template'])
    overrides = data.get('params', {})
    if not isinstance(overrides, dict) or set(overrides) - set(LIMITS):
        raise ValueError('策略参数无效')
    params = dict(base['params'], **overrides)
    for key, value in params.items():
        low, high, _ = LIMITS[key]
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('%s 须在 %s–%s 之间' % (key, low, high))
        if key == 'max_check' and value != int(value):
            raise ValueError('核验上限须为整数')
    params['max_check'] = int(params['max_check'])
    if base['id']==FIVE_ID and (params['min_pct']!=0 or params['prev_pct_max']!=3):
        raise ValueError('五层策略使用标准化涨幅，未使用的绝对涨幅/昨日涨幅参数须保留默认值')
    name = data.get('name') or base['name']
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
        raise ValueError('策略名称须为1–40字')
    result = dict(template=base['id'], name=name.strip(), params=params, rule_version='youzi-five-layer-v1.0.0' if base['id']==FIVE_ID else 'screen-v1')
    extra=validate_indicators(data.get('indicators',[]))
    if extra:result['indicators']=extra
    if 'five_rules' in data:
        if base['id']!=FIVE_ID:raise ValueError('五层指标须使用五层计算引擎')
        result['rule_version']='youzi-five-layer-v1.1.0'
        result['five_rules']=validate_rules(data['five_rules'])
        if not result['five_rules'] and not extra:raise ValueError('请至少选择一个筛选指标')
    result['config_hash'] = digest(result)
    return result


def metrics(history):
    rows = history['rows']; closes = [r['close'] for r in rows]
    if len(rows) < 60:
        raise ValueError('历史观测不足60条')
    if any(not isinstance(r[k], (float,int)) or isinstance(r[k],bool) or not math.isfinite(r[k])
           for r in rows for k in ('close','amount','pct')):
        raise ValueError('历史指标缺失或无效')
    if any(r['amount'] <= 0 or r['close'] <= 0 for r in rows):
        raise ValueError('历史价格或成交额无效')
    average = sum(r['amount'] for r in rows[-6:-1])/5
    return dict(ma20=sum(closes[-20:])/20, ma60=sum(closes[-60:])/60,
                close=closes[-1], return20=(closes[-1]/closes[-21]-1)*100,
                average5=average, volume_ratio=rows[-1]['amount']/average,
                pct=rows[-1]['pct'], prev_pct=rows[-2]['pct'])


def analyze(snapshot, config, history_getter=fetch_history, progress=lambda msg:None):
    p = config['params']; as_of = snapshot['as_of']; rows = snapshot['rows']
    usable, excluded, unavailable = [], [], []
    for s in rows:
        if 'ST' in s['name'].upper() or '退' in s['name']:
            excluded.append(dict(code=s['code'],name=s['name'],reason='ST或退市标记'))
        elif (not s.get('eod') or s['date'] != as_of or any(s.get(k) is None for k in ('close','pct','amount'))
              or s['close'] <= 0 or s['amount'] <= 0):
            unavailable.append(dict(code=s['code'],name=s['name'],reason='收盘时点、价格或成交额未取得有效值'))
        else:
            usable.append(s)
    if not usable:
        raise ValueError('没有可用于同日收盘计算的证券')
    market = dict(count=len(usable), up=sum(s['pct']>0 for s in usable), down=sum(s['pct']<0 for s in usable),
                  flat=sum(s['pct']==0 for s in usable), amount=sum(s['amount'] for s in usable),
                  mean_pct=sum(s['pct'] for s in usable)/len(usable), scope=snapshot['scope'])
    groups = {}
    for s in usable:
        if s['industry'] not in ('行业未知', '-', ''):
            groups.setdefault(s['industry'],[]).append(s)
    sectors = []
    for name, members in groups.items():
        sectors.append(dict(name=name,count=len(members),mean_pct=sum(s['pct'] for s in members)/len(members),
                            up_ratio=100*sum(s['pct']>0 for s in members)/len(members)))
    sectors.sort(key=lambda x:(-x['mean_pct'],x['name']))
    lookup = {}
    for i,s in enumerate(sectors):
        s['rank']=i+1;s['rank_pct']=100*(i+1)/len(sectors);lookup[s['name']]=s
    pool, pre_rejected = [], []
    for s in usable:
        reasons = []
        if s['amount'] < p['min_amount']*1e8: reasons.append('成交额低于设定下限')
        if s['pct'] < p['min_pct']: reasons.append('当日涨幅低于设定下限')
        sector = lookup.get(s['industry'])
        if config['template']=='sector':
            if not sector:
                unavailable.append(dict(code=s['code'],name=s['name'],reason='行业分类缺失，无法核验板块共振'))
                continue
            if sector['up_ratio'] < p['sector_up_min']: reasons.append('行业上涨占比不足')
            if sector['rank'] > max(1, math.ceil(len(sectors)*p['sector_top_pct']/100)): reasons.append('行业不在设定强度范围')
            if sector['mean_pct'] <= market['mean_pct']: reasons.append('行业等权涨幅未高于当前市场范围')
        if reasons: pre_rejected.append(dict(code=s['code'],name=s['name'],reasons=reasons))
        else: pool.append(s)
    pool.sort(key=lambda x:(-x['amount'],x['code']))
    checked = pool[:p['max_check']]; deferred = pool[p['max_check']:]
    histories, failures, evaluations = {}, {}, []
    def task(s):
        h = None
        try:
            h=history_getter(s,as_of)
            if h['rows'][-1]['date']!=as_of: raise ValueError('日K未更新至报告日期')
            dates=[r['date'] for r in h['rows']]
            if dates!=sorted(set(dates)) or any(d>as_of for d in dates): raise ValueError('日K日期错误')
            m=metrics(h)
            # Quote and K-line must describe the same close. Adjusted close itself may differ.
            if abs(m['pct']-s['pct'])>.15 or abs(h['rows'][-1]['amount']-s['amount'])/s['amount']>.03:
                raise ValueError('快照与日K涨幅或成交额不一致')
            return s,h,m,None
        except Exception as exc:
            return s,h,None,str(exc) if isinstance(exc,ValueError) else '历史数据核验失败'
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures=[executor.submit(task,s) for s in checked]
        for i,future in enumerate(as_completed(futures)):
            s,h,m,error=future.result()
            progress('历史核验 %d / %d；尚有 %d 只未纳入本轮核验' % (i+1,len(checked),len(deferred)))
            if h is not None: histories[s['code']]=h
            if error:
                failures[s['code']]=error
                evaluations.append(dict(stock=s,status='待核验',checks=[],metrics=None,reason=error))
                continue
            histories[s['code']]=h
            checks=[dict(name='前五日成交额倍数',value=m['volume_ratio'],operator='>=',target=p['volume_ratio'],passed=m['volume_ratio']>=p['volume_ratio']),
                    dict(name='前复权收盘价高于20期均线',value=m['close'],operator='>',target=m['ma20'],passed=m['close']>m['ma20']),
                    dict(name='20期均线高于60期均线',value=m['ma20'],operator='>',target=m['ma60'],passed=m['ma20']>m['ma60'])]
            if config['template']=='reversal':
                checks.append(dict(name='前一日涨幅不高于设定值',value=m['prev_pct'],operator='<=',target=p['prev_pct_max'],passed=m['prev_pct']<=p['prev_pct_max']))
            checks = [dict(name='当日成交额（亿元）',value=s['amount']/1e8,operator='>=',target=p['min_amount'],passed=True),
                      dict(name='当日涨幅（%）',value=s['pct'],operator='>=',target=p['min_pct'],passed=True)] + checks
            if config['template']=='sector':
                sector = lookup[s['industry']]
                checks += [dict(name='行业上涨占比（%）',value=sector['up_ratio'],operator='>=',target=p['sector_up_min'],passed=True),
                           dict(name='行业强度名次',value=sector['rank'],operator='<=',target=max(1,math.ceil(len(sectors)*p['sector_top_pct']/100)),passed=True),
                           dict(name='行业等权涨幅高于市场（%）',value=sector['mean_pct'],operator='>',target=market['mean_pct'],passed=True)]
            checks += indicator_checks(config,h)
            evaluations.append(dict(stock=s,status='符合条件' if all(c['passed'] for c in checks) else '未满足条件',
                                    metrics=m,checks=checks,reason='',basis=h['basis']))
    evaluations.sort(key=lambda e:({'符合条件':0,'待核验':1,'未满足条件':2}[e['status']],-e['stock']['amount'],e['stock']['code']))
    passed=[e for e in evaluations if e['status']=='符合条件']
    classified=sum(s['industry'] not in ('行业未知','-','') for s in usable)
    counts=dict(source=len(rows),market=len(usable),excluded=len(excluded),data_missing=len(unavailable),
                pre_rejected=len(pre_rejected),prefilter=len(pool),checked=len(checked),deferred=len(deferred),
                industry_covered=classified,industry_missing=len(usable)-classified,history_failed=len(failures),passed=len(passed),rejected=sum(e['status']=='未满足条件' for e in evaluations))
    layers=[
      dict(name=LAYERS[0],status='未接入',applicability='市场研究',support=['同日可用范围：上涨%d家，下跌%d家，平盘%d家。'%(market['up'],market['down'],market['flat'])],missing=['涨跌停、炸板率、连板梯度及昨日强势股反馈未接入'],refs=['snapshot.rows']),
      dict(name=LAYERS[1],status='统计已接入' if snapshot.get('industry_data') and sectors else '未接入',applicability='当前行业统计，未确认题材主线',support=['已计算%d个行业的等权涨幅与上涨占比。'%len(sectors)],missing=(['当前行情来源缺少行业分类，无法核验板块共振'] if not sectors else [])+['产业事件、连续性和题材成分关系尚未核验'],refs=['report.sectors','snapshot.rows']),
      dict(name=LAYERS[2],status='未接入',applicability='候选相对表现',support=['%d只满足所选数值条件；这不是龙头确认。'%len(passed)],missing=['带动因果、公司公告与完整同类比较尚未核验'],refs=['report.evaluations','histories']),
      dict(name=LAYERS[3],status='已核验' if len(checked)>len(failures) else '待核验',applicability='仅限本轮已完成的股票数值条件',support=['本轮核验%d只，其中%d只满足配置；规则配置已存档。'%(len(checked),len(passed))],missing=(['未核验及数据缺口见单独的覆盖说明，不改变已完成股票的条件结果'] if failures or deferred or unavailable else []),refs=['strategy','report.counts','report.evaluations']),
      dict(name=LAYERS[4],status='未接入',applicability='行情质量与研究风险；个人计划偏离不适用',support=['缺失数据没有补零；未核验股票没有当作已排除。'],missing=['公告事件、个人持仓和行为资料未取得；未评价个人交易纪律'],refs=['report.unavailable','report.failures','report.deferred'])]
    return dict(as_of=as_of,source=snapshot['source'],data_kind=snapshot['data_kind'],market=market,sectors=sectors,industry_data=snapshot.get('industry_data'),
                counts=counts,evaluations=evaluations,layers=layers,excluded=excluded,unavailable=unavailable,
                pre_rejected=pre_rejected,deferred=[dict(code=s['code'],name=s['name']) for s in deferred],
                failures=failures,histories=histories,
                summary='本轮%d只候选完成历史请求，%d只满足所选条件，%d只因历史缺失待核验，另有%d只未纳入历史核验。'%(len(checked),len(passed),len(failures),len(deferred)),
                limitations=snapshot.get('warnings',[])+['仅为当前沪深范围的收盘研究，不是历史回测或收益验证。',
                             '行业是来源当前分类，不等于题材主线；未知风险没有被视为不存在。',
                             '模板没有使用“涨停基因”；不是原平台游资心法规则的逐字复制。',
                             '成交额排序决定核验顺序，不表示收益预测；无模型调用。',
                             '历史指标采用前复权有效日K观测；缺交易日历，不能判断中间是否缺交易日。'],disclaimer=DISCLAIMER)


class ScreeningStore:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS screen_runs(id TEXT PRIMARY KEY, request_id TEXT UNIQUE, created_at TEXT, state TEXT, body TEXT);
            CREATE TABLE IF NOT EXISTS screen_strategies(id TEXT PRIMARY KEY,name TEXT,version INTEGER,body TEXT,UNIQUE(name,version));''')
    def recover(self):
        with self.store.connect() as db:
            rows=db.execute("SELECT body FROM screen_runs WHERE state='RUNNING'").fetchall()
        for r in rows:
            item=json.loads(r['body']);item.update(state='INTERRUPTED',message='上次运行中断；保留记录，不自动重试',finished_at=timestamp());self.save(item)
    def save(self,item):
        with self.store.connect() as db:
            db.execute('UPDATE screen_runs SET state=?,body=? WHERE id=?',(item['state'],encode(item),item['id']))
    def get(self,identifier):
        with self.store.connect() as db: row=db.execute('SELECT body FROM screen_runs WHERE id=?',(identifier,)).fetchone()
        if not row: raise ValueError('筛选记录不存在')
        return json.loads(row['body'])
    def list(self):
        with self.store.connect() as db: rows=db.execute('SELECT body FROM screen_runs ORDER BY created_at DESC LIMIT 50').fetchall()
        return [dict(id=x['id'],state=x['state'],created_at=x['created_at'],name=x['strategy']['name'],mode=x['mode'],as_of=x.get('report',{}).get('as_of',''),version=x['strategy'].get('saved_version'),passed=x.get('report',{}).get('counts',{}).get('passed')) for x in (json.loads(r['body']) for r in rows)]
    def strategies(self):
        with self.store.connect() as db: rows=db.execute('SELECT body FROM screen_strategies ORDER BY name,version DESC').fetchall()
        return [json.loads(r['body']) for r in rows]
    def save_strategy(self,data):
        s=strategy(data)
        with self.store.connect() as db:
            v=db.execute('SELECT COALESCE(MAX(version),0)+1 FROM screen_strategies WHERE name=?',(s['name'],)).fetchone()[0]
            s.update(id=str(uuid.uuid4()),saved_version=v)
            db.execute('INSERT INTO screen_strategies VALUES(?,?,?,?)',(s['id'],s['name'],v,encode(s)))
        return s
    def resolve_strategy(self,data):
        s=strategy(data)
        if data.get('saved_id'):
            with self.store.connect() as db:
                row=db.execute('SELECT body FROM screen_strategies WHERE id=?',(data['saved_id'],)).fetchone()
            if not row: raise ValueError('已保存的策略版本不存在')
            saved=json.loads(row['body'])
            if saved['config_hash']!=s['config_hash']: raise ValueError('参数已变化，请另存版本或取消已选版本')
            s.update(saved_id=saved['id'],saved_version=saved['saved_version'])
        return s
    def request_payload(self,data):
        force=data.get('force_refresh',False);parent=data.get('retry_of','')
        if not isinstance(force,bool) or not isinstance(parent,str):raise ValueError('刷新或重试参数无效')
        return dict(strategy=self.resolve_strategy(data),mode=data.get('mode','live'),force_refresh=force,retry_of=parent)
    def previous_screening(self,data):
        """Read-only lookup; no price download and no restriction to the newest 50 rows."""
        wanted=self.resolve_strategy(data);mode=data.get('mode','live')
        if mode not in ('live','demo'):raise ValueError('运行模式无效')
        with self.store.connect() as db:
            rows=db.execute('SELECT body FROM screen_runs ORDER BY created_at DESC,id DESC').fetchall()
        for row in rows:
            item=json.loads(row['body'])
            if item['mode']==mode and item['strategy']['config_hash']==wanted['config_hash'] and (item.get('report') or item['state']=='RUNNING'):
                return dict(id=item['id'],name=item['strategy']['name'],version=item['strategy'].get('saved_version'),as_of=item.get('report',{}).get('as_of',''),created_at=item['created_at'],state=item['state'])
        return None
    def duplicate(self,data):
        request_id=data.get('request_id')
        if not isinstance(request_id,str):return None
        with self.store.connect() as db:row=db.execute('SELECT body FROM screen_runs WHERE request_id=?',(request_id,)).fetchone()
        if not row:return None
        old=json.loads(row['body'])
        if old['payload_hash']!=digest(self.request_payload(data)):raise ValueError('相同请求标识不能用于不同配置')
        return old
    def start(self,data):
        payload=self.request_payload(data);mode=payload['mode'];s=payload['strategy']
        if mode not in ('live','demo'):raise ValueError('运行模式无效')
        if s['template']==FIVE_ID and mode!='live':raise ValueError('游资五层策略只接受真实公开数据，不提供示例替代')
        request_id=data.get('request_id','')
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9-]{16,80}',request_id):raise ValueError('请求标识无效')
        if payload['retry_of']:
            parent=self.get(payload['retry_of'])
            if parent['state']=='RUNNING' or parent['mode']!=mode or parent['strategy']['config_hash']!=s['config_hash']:
                raise ValueError('只能按原策略重试已结束或中断的任务')
        payload_hash=digest(payload)
        with self.store.connect() as db:
            row=db.execute('SELECT body FROM screen_runs WHERE request_id=?',(request_id,)).fetchone()
            if row:
                old=json.loads(row['body'])
                if old['payload_hash']!=payload_hash:raise ValueError('相同请求标识不能用于不同配置')
                return old,False
            item=dict(id=str(uuid.uuid4()),request_id=request_id,payload_hash=payload_hash,created_at=timestamp(),
                      state='RUNNING',message='检查本地数据',strategy=s,mode=mode,app_version=VERSION,
                      force_refresh=payload['force_refresh'],retry_of=payload['retry_of'],skill=resources(),model_used=False,
                      data_state=dict(phase='本地检查',requirements=['同日收盘行情','至少60期有效价格','此前5期成交额','对应复权资料'],stats={}))
            db.execute('INSERT INTO screen_runs VALUES(?,?,?,?,?)',(item['id'],request_id,item['created_at'],item['state'],encode(item)))
        if s['template']==FIVE_ID:
            item['data_state']['requirements']=['同日收盘快照','交易所日历及证券名录','实际限价与公开股池','行业与基准指数','61交易日OHLC','公告目录窗口']
            self.save(item)
        return item,True
    def execute(self,item,snapshot_getter=None,history_getter=None,industry_getter=None,five_resources=None):
        state_lock=threading.RLock();manager=None
        def progress(msg):
            with state_lock:
                item['message']=msg
                if manager:
                    item['data_state']['stats']=dict(manager.stats)
                    item['data_state']['recovery_events']=list(manager.recovery_events)
                    if 'snapshot' not in item and any(k in msg for k in ('行情','目录')):item['data_state']['phase']='获取同日行情'
                self.save(item)
        try:
            parent=self.get(item['retry_of']) if item.get('retry_of') else None
            if item['mode']=='demo':
                snapshot=(snapshot_getter or demo_snapshot)();get_history=history_getter or demo_history
            else:
                manager=MarketStore(self.store,item['id'],progress,item.get('force_refresh',False))
                if parent and parent.get('snapshot') and (parent.get('report',{}).get('failures') or parent['state']=='INTERRUPTED'):
                    snapshot=copy.deepcopy(parent['snapshot']);validate_snapshot(snapshot,datetime.now(CN))
                    item['data_state']['snapshot_origin']='原任务固定截止日'
                else:snapshot=manager.snapshot(snapshot_getter,refresh=bool(parent and parent.get('report',{}).get('counts',{}).get('data_missing')))
                completed={e['stock']['code'] for e in (parent or {}).get('report',{}).get('evaluations',[]) if e.get('metrics')}
                def get_history(stock,as_of):
                    if parent and stock['code'] in completed:
                        h=copy.deepcopy(parent['report']['histories'][stock['code']])
                        try:
                            validate_history(h,as_of,stock)
                            manager.tick('history_reused','复用原任务已完成历史 '+stock['code']);return h
                        except ValueError:pass
                    return manager.history(stock,as_of,history_getter or fetch_history)
                if parent and parent.get('snapshot') and snapshot['as_of']!=parent['snapshot']['as_of']:
                    raise ValueError('来源行情日期已变化，请新建筛选任务；原报告仍保留')
            item['snapshot']=snapshot
            if manager and item['strategy']['template'] in ('sector',FIVE_ID):
                item['data_state']['phase']='准备行业分类';progress('自动获取并核验申万一级行业分类')
                try:
                    snapshot=manager.industry(snapshot,industry_getter,refresh=bool(parent and (parent.get('report',{}).get('industry_data') or {}).get('failed_groups')))
                    item['data_state']['industry_data']={k:v for k,v in snapshot['industry_data'].items() if k not in ('unknown','conflicts')}
                    if item['data_state']['industry_data']['matched']==0:raise ValueError('行业分类未覆盖当前行情范围，无法核验板块共振')
                except ValueError as exc:
                    if item['strategy']['template']!=FIVE_ID:raise
                    item['data_state']['industry_error']=str(exc)
            if item['strategy']['template']=='sector' and not any(r.get('industry') not in ('行业未知','-',None,'') for r in snapshot['rows']):
                raise ValueError('当前来源未接入行业分类，板块共振暂不可用；请选择其他策略')
            item['snapshot']=snapshot
            gap_retry=snapshot.get('evidence',{}).get('quote_gap_recheck')
            if gap_retry:item['data_state']['quote_gap_recheck']={k:gap_retry[k] for k in ('attempted','recovered','remaining','as_of') if k in gap_retry}
            item['data_state'].update(phase='历史准备与校验',as_of=snapshot['as_of'],source=snapshot['source'],fetched_at=snapshot.get('fetched_at'))
            progress('行情已留档，开始历史核验')
            if item['strategy']['template']==FIVE_ID:
                from five_layer import run_five
                item['data_state']['phase']='五层公开资料与规则核验'
                report=run_five(snapshot,item['strategy'],manager,get_history,progress,five_resources)
                if item['data_state'].get('industry_error'):
                    report['five_layer']['required_data_missing']=True
                    report['five_layer']['sources'].append(dict(dataset='industry',status='待核验',required=True,reason=item['data_state']['industry_error']))
            else:report=analyze(snapshot,item['strategy'],get_history,progress)
            item['report']=report;item['state']='PARTIAL' if report['counts']['deferred'] or report['counts']['history_failed'] or report['counts']['data_missing'] or (report.get('industry_data') or {}).get('failed_groups') or (report.get('five_layer') or {}).get('required_data_missing') else 'COMPLETED'
            item['message']=report['summary'];item['finished_at']=timestamp();item['data_state']['phase']='已保存报告'
            item['data_state']['issues']=dict(quote_missing=report['counts']['data_missing'],history_failed=report['counts']['history_failed'],deferred=report['counts']['deferred'])
        except Exception as exc:
            import logging
            logging.getLogger('joyworkflow').exception('Public-data screening failed; details kept in local log')
            reason=str(exc) if isinstance(exc,ValueError) else '筛选失败；请检查网络和本地存储后手动重试'
            item['data_state']['error']=error_info(exc)
            item.update(state='FAILED',message=reason,finished_at=timestamp());item['data_state']['phase']='获取或校验失败'
            if manager:
                try:manager.issue('','run',reason)
                except Exception:pass
        if manager:item['data_state'].update(stats=dict(manager.stats),recovery_events=list(manager.recovery_events),cache_refs=dict(manager.refs))
        self.save(item);return item
