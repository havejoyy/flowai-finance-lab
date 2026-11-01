"""Run with microApp's Python environment. Read-only SELECT; no app startup."""
import argparse
import json
import os
from pathlib import Path
from engine import code


def candidate_code(value):
    """microApp stores six-digit A-share codes; reject other instruments."""
    value = str(value).strip().upper()
    if '.' in value:
        return code(value)
    if len(value) == 6 and value.isdigit():
        if value.startswith(('600', '601', '603', '605', '688', '689')):
            return value + '.SH'
        if value.startswith(('000', '001', '002', '003', '300', '301')):
            return value + '.SZ'
        if value.startswith(('4', '8', '92')):
            return value + '.BJ'
    raise ValueError('无法识别的 A 股候选代码，请明确补充交易所后缀：' + value)


def unpack(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def main():
    parser = argparse.ArgumentParser(description='只读导出 microApp 已保存的筛选候选')
    parser.add_argument('--date', required=True)
    parser.add_argument('--rule', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--definition-file', type=Path, required=True, help='该规则的确切条件说明 UTF-8 文件')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--horizons', default='5,10,20')
    parser.add_argument('--source', choices=['csv', 'tushare'], default='csv')
    parser.add_argument('--benchmark', default='000300.SH')
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('输出文件已存在；请使用新文件名，避免覆盖快照')
    from sqlalchemy import create_engine, text
    url = os.environ.get('JOY_MICROAPP_DATABASE_URL')
    if not url:
        raise SystemExit('请设置 JOY_MICROAPP_DATABASE_URL；建议使用只读数据库账户')
    try:
        engine = create_engine(url)
        with engine.connect() as db:
            rows = db.execute(text('SELECT stock_code, stock_name, score, result_rank, metrics_snapshot, matched_reasons, data_dates FROM stock_screen_result WHERE rule_code=:rule AND rule_version=:version AND as_of_date=:date ORDER BY result_rank'),
                              {'rule': args.rule, 'version': args.version, 'date': args.date}).mappings().all()
        engine.dispose()
    except Exception:
        raise SystemExit('数据库读取失败；请检查地址、账户权限、网络及表结构。未输出含凭证的异常。')
    if not rows:
        raise SystemExit('未找到候选。不能区分未运行与真实空结果；请在工作台手动建立已确认的空批次。')
    candidates = []
    for row in rows:
        c = candidate_code(row['stock_code'])
        candidates.append({'code': c, 'reason': json.dumps(unpack(row['matched_reasons']), ensure_ascii=False, default=str),
                           'snapshot': {k: unpack(row[k]) for k in ('stock_code', 'stock_name', 'score', 'result_rank', 'metrics_snapshot', 'data_dates')}})
    body = {'as_of': args.date, 'origin': 'microApp-stock_screen_result', 'note': '导出当前数据库状态；并非历史时点不可篡改证明',
            'plan': {'name': args.rule, 'version': args.version, 'definition': args.definition_file.read_text('utf-8-sig'),
                     'horizons': [int(x) for x in args.horizons.split(',')], 'source': args.source, 'benchmark': args.benchmark},
            'candidates': candidates}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(body, stream, ensure_ascii=False, indent=2, default=str)
    print('已导出 %s 个候选到 %s；请在本地工作台导入' % (len(candidates), args.output))


if __name__ == '__main__':
    main()
