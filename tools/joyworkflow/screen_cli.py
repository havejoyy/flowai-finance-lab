"""Run the independent screener; public quotes only, never calls a model."""
import argparse
import json
import uuid
from pathlib import Path
from app import instance_lock
from engine import Store, encode
from screening import ScreeningStore


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,required=True)
    parser.add_argument('--template',choices=['volume','reversal','sector','youzi-five-layer-breakout-v1'],default='volume')
    parser.add_argument('--max-check',type=int,default=None,help='覆盖模板核验上限；默认采用所选模板设置')
    parser.add_argument('--demo',action='store_true')
    args=parser.parse_args()
    if args.demo and args.template=='youzi-five-layer-breakout-v1':
        parser.error('游资五层共振·放量突破仅接受真实公开数据')
    with instance_lock(args.data_dir):
        store=ScreeningStore(Store(args.data_dir));store.recover()
        item,_=store.start(dict(template=args.template,params={} if args.max_check is None else {'max_check':args.max_check},mode='demo' if args.demo else 'live',request_id=str(uuid.uuid4())))
        result=store.execute(item)
        print(encode({k:result[k] for k in ['id','state','message']}))
        if result.get('report'): print(encode({'date':result['report']['as_of'],'counts':result['report']['counts']}))
        return 1 if result['state']=='FAILED' else 0


if __name__=='__main__':
    raise SystemExit(main())
