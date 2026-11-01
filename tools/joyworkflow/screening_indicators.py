"""Allowlisted optional conditions, calculated locally from saved daily bars."""
import math

INDICATORS = [
    dict(id='return20',name='20期涨跌幅',unit='%',min=-100,max=1000,default=0,operator='>=',help='最近收盘与20期前收盘比较，使用同一复权口径'),
    dict(id='deviation20',name='相对MA20乖离率',unit='%',min=-100,max=1000,default=12,operator='<=',help='(收盘价 / 20期均价 − 1) × 100'),
    dict(id='average20_amount',name='20期平均成交额',unit='亿元',min=0,max=10000,default=1,operator='>=',help='最近20条有效日K成交额均值，包含当日'),
]

def validate_indicators(value):
    if not isinstance(value,list) or len(value)>len(INDICATORS):raise ValueError('附加指标无效')
    result=[];seen=set();catalog={x['id']:x for x in INDICATORS}
    for row in value:
        if not isinstance(row,dict) or set(row)!={'id','operator','value'} or not isinstance(row['id'],str) or row['id'] not in catalog:raise ValueError('请选择支持的附加指标')
        spec=catalog[row['id']];v=row['value']
        if row['id'] in seen:raise ValueError('同一指标不能重复添加')
        if row['operator'] not in ('>=','<='):raise ValueError('附加指标比较方式无效')
        if isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or not spec['min']<=v<=spec['max']:raise ValueError(spec['name']+'阈值无效')
        result.append(dict(id=row['id'],operator=row['operator'],value=v));seen.add(row['id'])
    return sorted(result,key=lambda x:x['id'])

def indicator_checks(config,history):
    settings=config.get('indicators',[])
    if not settings:return []
    rows=history['rows'];catalog={x['id']:x for x in INDICATORS}
    closes=[r['close'] for r in rows];ma20=sum(closes[-20:])/20
    values=dict(return20=(closes[-1]/closes[-21]-1)*100,deviation20=(closes[-1]/ma20-1)*100,average20_amount=sum(r['amount'] for r in rows[-20:])/20/1e8)
    result=[]
    for row in settings:
        value=values[row['id']];passed=value>=row['value'] if row['operator']=='>=' else value<=row['value']
        result.append(dict(id='CUSTOM_'+row['id'],name=catalog[row['id']]['name']+'（'+catalog[row['id']]['unit']+'）',value=value,operator=row['operator'],target=row['value'],passed=passed,status='通过' if passed else '不通过',source='本地同口径日K',reason='用户添加的附加筛选条件'))
    return result
