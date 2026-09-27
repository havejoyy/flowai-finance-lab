#!/usr/bin/env python3
"""Validate agent-extracted observations and calculate auditable differences."""
import argparse
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys

FACTORS = {'元': Decimal(1), '万元': Decimal(10000), '百万元': Decimal(1000000), '亿元': Decimal(100000000),
           'percent': Decimal(1), '元/股': Decimal(1)}


def number(v):
    if isinstance(v, bool):
        raise ValueError('boolean is not a financial number')
    try:
        d = Decimal(str(v).replace(',', ''))
    except InvalidOperation as exc:
        raise ValueError(f'invalid number: {v}') from exc
    if not d.is_finite():
        raise ValueError('non-finite number')
    return d


def fmt(v):
    return format(v, '.2f') if v is not None else '—'


def compact(t):
    return re.sub(r'\s+', '', t)


def evidence(ref, docs, sources, value=None):
    doc = docs[ref['doc']]
    pages = sources[doc['source_id']]['pages']
    page = ref['page']
    if isinstance(page, bool) or not isinstance(page, int) or page < 1 or page > len(pages):
        raise ValueError('invalid physical PDF page')
    quote = ref['quote']
    if not quote or compact(quote) not in compact(pages[page - 1]):
        raise ValueError('evidence quote not found on source page')
    if value is not None:
        tokens = re.findall(r'(?<![\d.])[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d.])', quote)
        if number(value) not in [number(t) for t in tokens]:
            raise ValueError('value is absent from evidence quote')


def validate_header(data, sources):
    docs = data['documents']
    if set(docs) != {'new', 'old'}:
        raise ValueError('exactly new/old documents required')
    cur, prev = data['current_period'], data['prior_period']
    if len(cur) != 2 or len(prev) != 2:
        raise ValueError('period must contain start and end')
    c, p = [date.fromisoformat(x) for x in cur], [date.fromisoformat(x) for x in prev]
    if c[0] > c[1] or p[0] > p[1] or c[0] <= p[1]:
        raise ValueError('invalid or overlapping periods')
    if any(a.year != b.year + 1 or (a.month, a.day) != (b.month, b.day) for a, b in zip(c, p)):
        raise ValueError('compare adjacent years with the same period boundaries')
    for k, period in [('new', cur), ('old', prev)]:
        d = docs[k]
        if d['company'] != data['company'] or d['code'] != data['code']:
            raise ValueError('company identity mismatch')
        if d['period'] != period or d['report_type'] not in ('annual', 'half-year', 'quarterly'):
            raise ValueError('report identity/period mismatch')
        if d['source_id'] not in sources:
            raise ValueError('source document missing')
    if docs['new']['report_type'] != docs['old']['report_type']:
        raise ValueError('report type mismatch')


def calculate(data, sources):
    validate_header(data, sources)
    docs = data['documents']
    rows, seen = [], set()
    if not data['metrics']:
        raise ValueError('at least one metric required')
    for m in data['metrics']:
        if m['id'] in seen:
            raise ValueError('duplicate metric id')
        seen.add(m['id'])
        row = dict(m)
        row.update(status='未核实', reason='', change=None, change_pct=None, restatement_delta=None)
        obs = [m.get(k) for k in ('current', 'baseline', 'prior_original')]
        try:
            if m['kind'] not in ('flow', 'stock', 'ratio'):
                raise ValueError('unsupported metric kind')
            if any(o is None or o.get('value') is None for o in obs):
                raise ValueError('缺少本期、比较期或旧报告原值')
            vals, units, scopes, currencies = [], [], [], []
            for i, o in enumerate(obs):
                period = data['current_period'] if i == 0 else data['prior_period']
                expected = [period[1]] if m['kind'] == 'stock' else period
                if o['period'] != expected:
                    raise ValueError('指标报告期不一致')
                if o['source']['doc'] != ('old' if i == 2 else 'new'):
                    raise ValueError('本期/比较期须来自新报告，旧值须来自旧报告')
                evidence(o['source'], docs, sources, o['value'])
                unit = o['unit']
                if unit not in FACTORS:
                    raise ValueError('不支持的单位')
                if o['scope'] not in ('consolidated', 'parent'):
                    raise ValueError('scope not recognized')
                vals.append(number(o['value']) * FACTORS[unit])
                units.append('元' if unit in ('元', '万元', '百万元', '亿元') else unit)
                scopes.append(o['scope'])
                currencies.append(o['currency'])
            if len(set(units)) != 1 or len(set(scopes)) != 1 or len(set(currencies)) != 1:
                raise ValueError('单位维度、合并口径或币种不一致')
            if (m['kind'] == 'ratio') != (units[0] == 'percent'):
                raise ValueError('ratio must use percent; other metrics cannot use percent')
            if m.get('comparable') is not True:
                raise ValueError('可比性未经确认：' + m.get('comparability_note', '未说明'))
            a, b, original = vals
            row.update(normalized_current=str(a), normalized_baseline=str(b),
                       normalized_prior_original=str(original), normalized_unit=units[0])
            if b != original:
                row['restatement_delta'] = str(b - original)
                restatement = m.get('restatement') or {}
                if restatement.get('confirmed') is not True:
                    raise ValueError('新旧比较基数不一致；待核实重述或提取错误')
                if restatement['source']['doc'] != 'new':
                    raise ValueError('重述说明须来自新报告')
                evidence(restatement['source'], docs, sources)
            if m.get('explanation'):
                evidence(m['explanation']['source'], docs, sources)
            row['change'] = str(a - b)
            row['change_unit'] = '百分点' if units[0] == 'percent' else units[0]
            if units[0] != 'percent' and b > 0:
                row['change_pct'] = str((a - b) / b * 100)
            row['status'] = '可比（已核实重述）' if b != original else '可比'
            if units[0] != 'percent' and b <= 0:
                row['reason'] = '上期基数非正，仅报告绝对变化，不给普通同比增长率'
        except (ValueError, KeyError, TypeError) as exc:
            row['reason'] = str(exc)
            row['change'] = row['change_pct'] = None
        rows.append(row)
    return {'company': data['company'], 'code': data['code'], 'current_period': data['current_period'],
            'prior_period': data['prior_period'], 'documents': docs,
            'coverage': {'input': len(rows), 'comparable': sum(r['status'].startswith('可比') for r in rows)},
            'metrics': rows}


def markdown(result):
    def loc(ref):
        doc = result['documents'][ref['doc']]
        return f"{ref['doc']} PDF p.{ref['page']}"
    lines = [f"# {result['company']}｜财报变化清单", '',
             f"本期：{'—'.join(result['current_period'])}；比较期：{'—'.join(result['prior_period'])}",
             f"覆盖：{result['coverage']['comparable']}/{result['coverage']['input']}项可比。仅覆盖所录指标，不表示全报无遗漏。", '',
             '| 指标 | 本期 | 新报告比较基数 | 旧报告原值 | 绝对变化 | 同比 | 状态 |',
             '|---|---:|---:|---:|---:|---:|---|']
    for r in result['metrics']:
        def cell(k):
            o = r.get(k)
            return '未核实' if not o or o.get('value') is None else f"{o['value']} {o['unit']}"
        change = '—' if r['change'] is None else fmt(number(r['change'])) + ' ' + r['change_unit']
        pct = '—' if r['change_pct'] is None else fmt(number(r['change_pct'])) + '%'
        lines.append(f"| {r['label']} | {cell('current')} | {cell('baseline')} | {cell('prior_original')} | {change} | {pct} | {r['status']} |")
    for r in result['metrics']:
        lines += ['', f"## {r['label']}"]
        if r['reason']:
            lines.append('核对说明：' + r['reason'])
        if r.get('comparability_note'):
            lines.append('可比性说明：' + r['comparability_note'])
        if r.get('restatement_delta') is not None:
            lines.append('比较基数与旧报告原值差额：' + r['restatement_delta'] + ' ' + r['normalized_unit'])
        for k, title in [('current', '本期'), ('baseline', '比较期'), ('prior_original', '旧值')]:
            o = r.get(k)
            if o and o.get('source'):
                lines.append(f"- {title}证据：{loc(o['source'])}；{o['source'].get('quote', '')}")
        e = r.get('explanation')
        if e:
            lines.append(f"公司披露解释（须复核语义，非独立因果验证）：{e['summary']}（{loc(e['source'])}）")
        lines.append('待核实：' + r.get('question', '需补充具体问题'))
    lines += ['', '## 来源与边界']
    for k, d in result['documents'].items():
        lines.append(f"- {k}：{d.get('url', '用户上传')}；获取日期 {d.get('retrieved_at', '未记录')}；审计 {d.get('audit', '未记录')}。")
    lines.append('本结果由 Agent 提取并确认语义，脚本核验证据匹配与计算；数值出现于原页不等于行列选择正确。仅用于信息整理，非投资建议。')
    return '\n'.join(lines) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input'); p.add_argument('--sources', required=True); p.add_argument('--output', required=True)
    args = p.parse_args()
    try:
        result = calculate(json.loads(Path(args.input).read_text()), json.loads(Path(args.sources).read_text()))
        output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
        (output / 'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        (output / '变化清单.md').write_text(markdown(result), encoding='utf-8')
        print(json.dumps(result['coverage'], ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f'Input error: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
