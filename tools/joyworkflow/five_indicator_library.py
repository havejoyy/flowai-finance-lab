"""Implemented five-layer condition catalog and explicit strategy rule selection."""
import copy
import math

LAYER_NAMES=['市场环境','行业共振','活跃代表','价格结构','风险约束']
def item(key,layer,name,unit,low,high,value,op,help,**kw):
    operators=kw.pop('operators',['>=','<=','>','<','='] if not kw.get('boolean') else ['='])
    return dict(id=key,layer=layer,layer_name=LAYER_NAMES[layer-1],scope='five',name=name,unit=unit,min=low,max=high,default=value,operator=op,operators=operators,help=help,**kw)
FIVE_INDICATORS=[
 item('M1',1,'市场上涨占比','%',0,100,40,'>=','上涨证券数 ÷ 合资格同日有效证券数 × 100'),
 item('M2',1,'市场跌停占比','%',0,100,1,'<=','按同日实际跌停价计算的跌停数 ÷ 有效证券数 × 100'),
 item('M3',1,'炸板率','%',0,100,45,'<=','炸板未封数 ÷ (收盘涨停数 + 炸板未封数) × 100；无触板样本不适用'),
 item('M4',1,'昨日涨停今日反馈','%',-30,30,-1,'>=','昨日涨停集合在今日的涨幅中位数；无合资格样本不适用'),
 item('S1',2,'行业当日强度排名','前%',1,100,20,'<=','行业等权涨幅竞赛排名；阈值按行业数向上取整对应名次',rank_fraction=True,operators=['<=']),
 item('S2',2,'行业上涨占比','%',0,100,55,'>=','行业上涨成员数 ÷ 同日有效成员数 × 100'),
 item('S3',2,'行业实际涨停家数','只',0,1000,2,'>=','同日实际涨停集合与行业合资格成员交集',integer=True),
 item('S4',2,'行业5日超额收益','百分点',-100,100,1,'>=','申万行业指数5日涨跌幅 − 沪深300同区间涨跌幅'),
 item('S5',2,'行业近5日上涨天数','天',0,5,3,'>=','申万行业指数最近5个交易日中收盘高于前收的天数',integer=True),
 item('C1',3,'当日成交额','亿元',0.1,100,2,'>=','同日收盘快照成交额'),
 item('C2',3,'行业内成交额排名','前%',1,100,20,'<=','行业成交额竞赛排名；按有效成员数向上取整对应名次',rank_fraction=True,operators=['<=']),
 item('C3',3,'个股相对行业5日表现','百分点',-100,100,2,'>=','个股同口径前复权5日涨跌幅 − 行业指数5日涨跌幅'),
 item('C4a',3,'收盘价 / MA20','倍',0.1,10,1,'>','同口径前复权收盘价 ÷ 最近20交易日收盘均价'),
 item('C4b',3,'MA20 / MA60','倍',0.1,10,1,'>','同口径20交易日收盘均价 ÷ 60交易日收盘均价'),
 item('C5',3,'标准化当日涨幅','倍',-1,1,0.3,'>=','当日原始涨幅 ÷ 该证券经实际上下限核对的适用涨幅限制'),
 item('P1',4,'收盘价 / 前20日最高价','倍',0.1,10,1,'>','同复权收盘价 ÷ 此前20交易日最高价，窗口不含当日'),
 item('P2',4,'前五日成交额倍数','倍',0.1,100,1.5,'>=','当日成交额 ÷ 此前5交易日成交额均值，不含当日'),
 item('P3',4,'日内收盘位置','比例',0,1,0.7,'>=','(收盘−最低)/(最高−最低)，最高等于最低时结构不通过'),
 item('R1',5,'上市交易日数下界','天',120,250,120,'>=','按已覆盖日历和真实上市日计数的下界；合资格范围仍排除上市不足120交易日',integer=True,operators=['>=']),
 item('R2',5,'20日平均成交额','亿元',0,10000,1,'>=','最近20条有效日K成交额均值，包含当日'),
 item('R3',5,'标准化ATR14','倍',0,10,0.5,'<=','(14日简单平均真实波幅 / 收盘价) ÷ 适用涨幅限制'),
 item('R4',5,'相对MA20乖离率','%',-100,1000,12,'<=','(收盘价 / MA20 − 1) × 100'),
 item('R5',5,'非一字有效价格区间','',1,1,1,'=','最高价大于最低价；1表示满足',boolean=True),
 item('R6',5,'公告标题窗口核验','',1,1,1,'=','最近5交易日起至信息截止的公告目录完整、时间明确且无待复核关键词；命中或缺失待核验。1表示满足',boolean=True),
]
# Data integrity is displayed but cannot be disabled or reduced by a strategy.
QUALITY_INDICATORS=[
 dict(id=k,scope='quality',layer=l,layer_name=LAYER_NAMES[l-1],name=n,help=h,readonly=True)
 for k,l,n,h in [
 ('G1',1,'交易日历与最近交易日','完整覆盖日历，行情日须为最近交易日'),('G2',1,'交易所证券名录','名录与上市日期可用'),
 ('G2a',1,'证券名录匹配率','至少98%'),('G3',1,'合资格行情覆盖率','至少98%；缺口另列'),('G4',1,'实际限价行情覆盖率','至少98%'),('G5',1,'沪深300历史','真实有效历史基准可用'),
 ('SQ1',2,'行业行情覆盖率','至少95%'),('SQ2',2,'行业有效成员数','至少10只'),('SQ3',2,'行业与基准日期对齐','最近6个观测与交易日历一致'),
 ('HQ',4,'个股OHLC连续与一致','最近61交易日连续、正数有效且价格口径一致'),
 ]]
CATALOG={x['id']:x for x in FIVE_INDICATORS}

def defaults(params):
    rows=[dict(id=x['id'],operator=x['operator'],value=x['default']) for x in FIVE_INDICATORS]
    mapped={'C1':'min_amount','P2':'volume_ratio','S2':'sector_up_min','S1':'sector_top_pct'}
    for row in rows:
        if row['id'] in mapped:row['value']=params[mapped[row['id']]]
    return rows

def validate_rules(rows):
    if not isinstance(rows,list) or not 0<=len(rows)<=len(CATALOG):raise ValueError('请至少选择一个五层量化指标')
    result=[];seen=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'id','operator','value'} or not isinstance(row['id'],str) or row['id'] not in CATALOG:raise ValueError('五层指标无效或属于固定数据校验项')
        spec=CATALOG[row['id']];v=row['value']
        if row['id'] in seen:raise ValueError('五层指标不能重复添加')
        if row['operator'] not in spec['operators']:raise ValueError(spec['name']+'比较方式无效')
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not spec['min']<=v<=spec['max'] or (spec.get('integer') and v!=int(v)):raise ValueError(spec['name']+'阈值无效')
        result.append(dict(row));seen.add(row['id'])
    return sorted(result,key=lambda x:x['id'])

class RuleBook:
    def __init__(self,config):
        self.rows=copy.deepcopy(config.get('five_rules',defaults(config['params'])))
        self.selected={x['id']:x for x in self.rows}
    def enabled(self,key):return key in self.selected
    def apply(self,checks,sizes=None):
        for c in checks:
            key=c['id']
            if key not in CATALOG:continue
            c['selected']=self.enabled(key)
            if not self.enabled(key):
                c.update(status='不适用',passed=True,reason='本策略未选择该指标');continue
            setting=self.selected[key];value=c['value'];target=setting['value'];op=setting['operator'];spec=CATALOG[key]
            if spec.get('rank_fraction'):
                c['configured_percent']=target
                target=max(1,math.ceil((sizes or {})[key]*target/100))
                c['reason']='配置前%s%%，对应名次门槛%s'%(setting['value'],target)
            c.update(operator=op,target=target)
            if value is None:
                # Explicit empty-universe and flat-range decisions are retained;
                # an unselected rule never controls the result.
                if c['status'] not in ('不适用','不通过','未进入'):c.update(status='待核验',passed=None)
                continue
            passed={'>=':lambda:value>=target,'<=':lambda:value<=target,'>':lambda:value>target,'<':lambda:value<target,'=':lambda:value==target}[op]()
            c.update(passed=passed,status='通过' if passed else '不通过')
        return checks
