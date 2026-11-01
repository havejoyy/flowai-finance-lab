"""Public SW level-one industry constituents; current observation, not historical membership."""
import copy
import json
import re
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime
from urllib.parse import urlencode
from engine import CN,timestamp,digest
from screening_data import public_bytes

BASE='https://www.swsresearch.com/institute-sw/api/index_publish/'
TAXONOMY='申万一级行业（2021版指数体系，当前成分）'

def sw_json(path,params):
    url=BASE+path+'?'+urlencode(params)
    data=json.loads(public_bytes(url,'https://www.swsresearch.com/'))
    if not isinstance(data,dict) or str(data.get('code'))!='200' or not isinstance(data.get('data'),dict):
        raise ValueError('申万行业接口响应异常')
    return data['data'],dict(url=url,fetched_at=timestamp(),data=data)

def pages(path,params,getter):
    rows=[];evidence=[];total=None
    for page in range(1,100):
        data,raw=getter(path,dict(params,page=page,page_size=2000))
        n=data.get('count');results=data.get('results')
        if not isinstance(n,int) or isinstance(n,bool) or not 1<=n<=15000 or (total is not None and n!=total):
            raise ValueError('申万成分总数无效或分页期间变化')
        total=n
        if not isinstance(results,list) or len(results)!=min(2000,total-len(rows)):
            raise ValueError('申万行业数据分页不完整')
        rows.extend(results);evidence.append(raw)
        if len(rows)==total:return rows,evidence
    raise ValueError('申万行业分页超限')

def parse_catalog(rows):
    groups={};names=set()
    for row in rows:
        code=row.get('swindexcode');name=row.get('swindexname')
        if not isinstance(code,str) or not re.fullmatch(r'801\d{3}',code) or code in groups or not isinstance(name,str) or not name.strip() or name in names:
            raise ValueError('行业目录代码、名称无效或重复')
        groups[code]=name.strip();names.add(name)
    if not groups:raise ValueError('行业目录为空')
    return groups

def parse_members(rows):
    members={}
    for row in rows:
        code=row.get('stockcode');name=row.get('stockname');begin=row.get('beginningdate')
        if not isinstance(code,str) or not re.fullmatch(r'\d{6}',code) or code in members or not isinstance(name,str) or not name.strip() or not isinstance(begin,str):
            raise ValueError('行业成员证券代码或名称无效、重复或缺少计入日期')
        day=datetime.fromisoformat(begin).date().isoformat()
        members[code]=dict(name=name.strip(),begin=day)
    return members

def fetch_industries(progress=lambda msg:None,getter=sw_json):
    rows,raw=pages('current/',{'indextype':'一级行业'},getter);catalog=parse_catalog(rows)
    groups={};failed={};evidence={'catalog':raw,'members':{}}
    def fetch(code):
        try:
            rows,raw=pages('details/component_stocks/',{'swindexcode':code},getter)
            return code,parse_members(rows),raw,None
        except Exception as exc:return code,None,None,str(exc) if isinstance(exc,ValueError) else '行业成分获取或解析失败'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(fetch,code) for code in catalog]
        for i,f in enumerate(as_completed(futures)):
            code,members,raw,error=f.result()
            if error:failed[code]=dict(name=catalog[code],reason=error)
            else:groups[code]=dict(name=catalog[code],members=members);evidence['members'][code]=raw
            progress('申万行业分类 %d / %d，失败 %d 个行业'%(i+1,len(catalog),len(failed)))
    # Compare identifiers/names only, not changing quote fields in the catalog.
    rows,final_raw=pages('current/',{'indextype':'一级行业'},getter)
    if parse_catalog(rows)!=catalog:raise ValueError('分类目录在获取期间变化，请重试')
    evidence['catalog_after']=final_raw
    if not groups:raise ValueError('行业分类全部获取失败，请稍后重试')
    result=dict(taxonomy=TAXONOMY,observed_at=timestamp(),observed_date=datetime.now(CN).date().isoformat(),
                catalog=catalog,groups=groups,failed_groups=failed,evidence=evidence)
    validate_industries(result)
    return result

def validate_industries(data):
    if data.get('taxonomy')!=TAXONOMY or not isinstance(data.get('catalog'),dict) or not data['catalog'] or not isinstance(data.get('groups'),dict) or not data['groups']:
        raise ValueError('本地行业分类格式无效')
    if set(data['groups']) & set(data.get('failed_groups',{})) or set(data['groups']) | set(data.get('failed_groups',{})) != set(data['catalog']):
        raise ValueError('行业分类范围不完整')
    datetime.fromisoformat(data['observed_at'])
    for code,g in data['groups'].items():
        if g.get('name')!=data['catalog'][code] or not isinstance(g.get('members'),dict) or not g['members']:
            raise ValueError('行业名称或成分缺失')
        for c,r in g['members'].items():
            if not re.fullmatch(r'\d{6}',c):raise ValueError('本地行业证券代码无效')
            datetime.strptime(r['begin'],'%Y-%m-%d')
    return data

def attach_industries(snapshot,data,batch_id=None):
    validate_industries(data);snap=copy.deepcopy(snapshot);mapping={};future=[]
    for industry,group in data['groups'].items():
        for code,member in group['members'].items():
            if member['begin']>snap['as_of']:future.append(code);continue
            mapping.setdefault(code,[]).append((industry,group['name']))
    unknown=[];conflicts=[];matched=0
    for row in snap['rows']:
        options=mapping.get(row['code'],[])
        row['industry']='行业未知';row.pop('industry_code',None)
        if len(options)==1:
            row['industry_code'],row['industry']=options[0];matched+=1
        else:
            issue=dict(code=row['code'],name=row['name'],reason='行业归属冲突' if len(options)>1 else '当前申万成分未覆盖或对应行业未取得')
            (conflicts if len(options)>1 else unknown).append(issue)
    snap['industry_data']=dict(taxonomy=data['taxonomy'],observed_at=data['observed_at'],observed_date=data['observed_date'],
        batch_id=batch_id,industry_count=len(data['groups']),catalog_count=len(data['catalog']),matched=matched,
        total=len(snap['rows']),coverage_pct=100*matched/len(snap['rows']),unknown=unknown,conflicts=conflicts,
        failed_groups=data['failed_groups'],future_members_ignored=len(future),
        basis='申万当前成分与来源行情范围交集；非历史时点行业分类。行业涨幅按本轮同日有效成员等权计算。')
    snap.setdefault('evidence',{})['industry_classification']=dict(batch_id=batch_id,hash=digest(data),data=data)
    # The snapshot provider's lack-of-industry warning is superseded explicitly.
    snap['warnings']=[w for w in snap.get('warnings',[]) if '未提供行业分类' not in w]
    if unknown or conflicts or data['failed_groups']:
        snap['warnings'].append('行业分类存在未覆盖或冲突，缺口单列；仅在已取得分类及有效行情的范围内计算共振。')
    snap['warnings'].append('行业分类获取日期 '+data['observed_date']+'；当前成分不能当作历史时点成分用于回测。')
    return snap
