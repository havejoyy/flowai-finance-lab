#!/usr/bin/env python3
"""Zero-data-setup public-data panic-pressure workflow."""
import argparse
from datetime import datetime, timezone, timedelta
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


def progress(message):
    print(message,file=sys.stderr,flush=True)


def ensure_runtime(cache):
    if all(importlib.util.find_spec(name) is not None for name in ('numpy','h5py')): return
    env=Path(cache)/'runtime'
    python=env/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if Path(sys.prefix).resolve()==env.resolve(): raise ValueError('PUBLIC_DEPENDENCY_INSTALL_INCOMPLETE')
    if not python.exists():
        progress('首次安装计算依赖到Skill专用缓存，不修改系统Python。')
        subprocess.run([sys.executable,'-m','venv',str(env)],check=True,stdout=sys.stderr,timeout=120)
    subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','-r',
                    str(Path(__file__).resolve().parents[1]/'requirements-public.txt')],
                   check=True,stdout=sys.stderr,timeout=240)
    code=subprocess.call([str(python),'-B',str(Path(__file__).resolve()),*sys.argv[1:]])
    raise SystemExit(code)


def render(result):
    from panic_pressure import formatted
    r=result
    lines=['# A股恐慌压力（公开数据版）','',
           f"数据日：{r['tradeDate']}｜查询日：{r['checkedOn']}｜{r['status']}",
           f"最近公开行情日：{r['latestSnapshotDate']}；来源按月更新。"]
    if r['score'] is not None:
        lines += ['',f"**恐慌压力：{formatted(r['score'])} / 100**",'',
                  '| 分项 | 分数 |','|---|---:|']
        for k,label in [('intensity','强度：下跌深浅'),('breadth','扩散：下跌范围'),('selling','抛压：极端下跌')]:
            lines.append(f"| {label} | {formatted(r['dimensions'][k])} |")
        lines += ['',f"有效当日样本：{r['coverage']['received']}只；各因子参考日数：{min(r['referenceCounts'].values())}—{max(r['referenceCounts'].values())}。"]
    else: lines += ['','数据未达到完整计算门槛，本次没有有效分数。']
    lines += ['',f"计算版本：{r['methodVersion']}；本地自动获取、计算，无需上传数据。",
              '**口径区别：以公开数据包中当前存续股票回看历史，存在幸存者偏差；不能与原版分数直接比较或拼接。**',
              f"数据距查询日{r['dataAgeCalendarDays']}个自然日，不代表今天盘面。",
              '分数是市场价格行为相对历史的压力，非下跌概率、投资建议或恐慌人数比例。',
              f"来源：{r['sourceUrl']}",f"结果校验值：{r['resultHash']}"]
    if not r['sourceNetworkChecked']: lines+=['本次使用明确指定的离线缓存，未检查上游更新。']
    return '\n'.join(lines)+'\n'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--date',default='latest')
    p.add_argument('--cache-dir',type=Path,default=Path(os.environ.get('XDG_CACHE_HOME',str(Path.home()/'.cache')))/'panic-pressure-index')
    p.add_argument('--json',action='store_true')
    p.add_argument('--offline',action='store_true',help='Explicitly reuse an already downloaded bundle')
    a=p.parse_args()
    try:
        a.cache_dir=a.cache_dir.expanduser().resolve();a.cache_dir.mkdir(parents=True,exist_ok=True)
        ensure_runtime(a.cache_dir)
        # A nonblocking process lock prevents cache collisions, without stale lockfiles on POSIX.
        if os.name=='posix':
            import fcntl
            lock=(a.cache_dir/'run.lock').open('a+')
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise ValueError('ANOTHER_CALCULATION_IS_RUNNING')
        from public_source import acquire,write_json
        from public_compute import prepare,score
        today=datetime.now(timezone(timedelta(hours=8))).date()
        root,source=acquire(a.cache_dir,today,offline=a.offline,progress=progress)
        prepared,calendar=prepare(root,a.cache_dir/'computed',progress=progress)
        result=score(prepared,calendar,a.date,today.isoformat(),source)
        # Keep a separate audit output; never mistake it for an upstream observation.
        saved=json.loads(json.dumps(result,default=str))
        write_json(a.cache_dir/'last-result.json',saved)
        print(json.dumps(saved,ensure_ascii=False,indent=2) if a.json else render(result))
        return 0 if result['status']=='READY' else 2
    except Exception as exc:
        print(json.dumps(dict(status='UNAVAILABLE',score=None,error=str(exc)),ensure_ascii=False))
        return 2


if __name__=='__main__': sys.exit(main())
