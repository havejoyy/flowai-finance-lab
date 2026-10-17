"""Explicit public-data variant: frozen current survivors, never mixed with V1."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from methodology import METHOD as ORIGINAL
METHOD=copy.deepcopy(ORIGINAL)
METHOD.update(version='CN_PANIC_PUBLIC_V1',universeVersion='RQ_SNAPSHOT_SURVIVORS_SHSZ_120D_V1',
              name='A股恐慌压力（公开数据版）',scope='公开月度包中存续沪深普通A股；逐日排除ST/停牌/上市不足120日',
              initializationRawSessions=400,
              historyMode='CURRENT_SURVIVORS_RECONSTRUCTED')
METHOD['limitations'] += ['使用数据包当前存续股票回看历史，存在幸存者偏差；不是历史全市场指数。',
                         '公开月度包可能滞后，分数仅代表标注的数据日，不是实时或每日更新。',
                         '当前证券状态取自供应商元数据，未逐股复核交易所退市整理公告。',
                         '每个新数据包用同批次股票池重算历史，不能与原CN_PANIC_V1或其他批次直接拼接。']
METHOD_HASH=hashlib.sha256(json.dumps(METHOD,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
# Independent namespace, shared vetted arithmetic. Does not mutate legacy module globals.
spec=importlib.util.spec_from_file_location('_public_panic_calculator',Path(__file__).with_name('calculator.py'))
engine=importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
engine.METHOD=METHOD
engine.METHOD_HASH=METHOD_HASH
