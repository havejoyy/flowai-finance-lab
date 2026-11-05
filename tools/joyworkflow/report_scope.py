"""Read-only presentation of screening coverage, including legacy saved reports."""


def quote_gap_reason(stock, as_of):
    reasons = []
    if not stock.get('eod'):
        reasons.append('未取得有效收盘时点')
    if stock.get('date') != as_of:
        reasons.append('报价日期 %s 与报告日期 %s 不一致' % (stock.get('date') or '缺失', as_of))
    for key, name in (('close', '收盘价'), ('pct', '涨跌幅'), ('amount', '成交额')):
        value = stock.get(key)
        if value is None:
            reasons.append(name + '缺失')
        elif key in ('close', 'amount') and value <= 0:
            reasons.append(name + '不大于零（原因待数据源核实）')
    return '；'.join(reasons)


def report_scope(item):
    """Derive display fields without rewriting archived report counts or evidence."""
    report = item['report']
    counts = report['counts']
    five = report.get('five_layer') or {}
    coverage = five.get('coverage') or {}
    snapshot = item.get('snapshot') or {}
    stocks = {s['code']: s for s in snapshot.get('rows', [])}
    evaluations = report.get('evaluations', [])
    names = {s['code']: s.get('name', '') for s in stocks.values()}
    names.update({e['stock']['code']: e['stock'].get('name', '') for e in evaluations})
    gaps = {}

    def add(row, kind, reason=None):
        code = row.get('code')
        if not code:
            return
        gap = gaps.setdefault(code, dict(code=code, name=row.get('name') or names.get(code, ''),
                                        categories=[], reasons=[]))
        if kind not in gap['categories']:
            gap['categories'].append(kind)
        reason = reason or row.get('reason') or '原记录未保存具体原因，需重新获取核验'
        if reason not in gap['reasons']:
            gap['reasons'].append(reason)

    for row in report.get('unavailable', []):
        reason = quote_gap_reason(stocks[row['code']], report['as_of']) if row['code'] in stocks else ''
        add(row, '行业分类' if '行业' in row.get('reason', '') else '行情或名录', reason)
    for row in coverage.get('missing_eligible', []):
        reason = quote_gap_reason(stocks[row['code']], report['as_of']) if row['code'] in stocks else ''
        add(row, '合资格行情', reason)
    for code, reason in report.get('failures', {}).items():
        add(dict(code=code), '历史或个股资料', reason)
    for e in evaluations:
        if e.get('status') == '待核验':
            pending = [c.get('reason') or c.get('name') or '指标资料缺失'
                       for c in e.get('checks', []) if c.get('status') == '待核验' and c.get('selected') is not False]
            add(e['stock'], '指标核验', e.get('reason') or '；'.join(pending) or None)

    # Only classify missing industry data as a screening gap when this engine uses it.
    if item.get('strategy', {}).get('template') in ('sector', 'youzi-five-layer-breakout-v1'):
        industry = report.get('industry_data') or {}
        excluded = {s['code'] for s in report.get('excluded', [])}
        for row in industry.get('unknown', []) + industry.get('conflicts', []):
            if row['code'] not in excluded:
                add(row, '行业分类')

    resources = [dict(dataset=s.get('dataset', '公开资料'), reason=s.get('reason') or '资料待核验',
                      required=bool(s.get('required')))
                 for s in five.get('sources', []) if s.get('status') == '待核验']
    if item.get('strategy', {}).get('template') in ('sector', 'youzi-five-layer-breakout-v1'):
        for group, reason in (report.get('industry_data') or {}).get('failed_groups', {}).items():
            message = reason.get('reason') if isinstance(reason, dict) else reason
            resources.append(dict(dataset='行业分类 ' + group, reason=message or '行业资料获取失败', required=True))
    missing_eligible = coverage.get('missing_eligible', [])
    outside = sum(row.get('reason') == '未在行情目录中' for row in missing_eligible)
    source_invalid = counts['source'] - counts['excluded'] - counts['market']
    notes = ['范围仅为本次来源覆盖的沪深 A 股，不含北交所；不是全市场完整筛选。',
             '有效证券只表示可参与同日市场统计，不代表已逐只完成五层及历史核验。',
             '数据缺口按证券代码去重，可与有效证券重叠，也可包含来源目录之外的证券；不可与其他卡片直接相加减。']
    if report.get('data_kind') == 'synthetic':
        notes[0] = '这是虚构示例，仅演示本次示例证券范围，不代表真实行情或全市场筛选。'
    if source_invalid >= 0:
        notes.append('来源内：%s = 基础规则排除 %s + 同日有效 %s + 行情或名录无效 %s。'
                     % (counts['source'], counts['excluded'], counts['market'], source_invalid))
    else:
        notes.append('原报告计数口径无法直接对账，请检查完整记录，未推算缺失数量。')
    if coverage.get('eligible_expected') is not None:
        expected = coverage['eligible_expected']
        valid = coverage.get('quote_valid', counts['market'])
        notes.append('合资格名录 %s 只；有效报价 %s 只；合资格报价缺口 %s 只（其中目录外 %s 只）。'
                     % (expected, valid, len(missing_eligible), outside))
    if counts.get('deferred'):
        notes.append('%s 只初筛候选因本轮核验上限尚未核验，不计为不满足条件。' % counts['deferred'])
    if any(s.get('status') == '未进入' for s in five.get('stages', [])):
        notes.append('部分后续层级未进入，详见五层阶段状态；零入选不等于所有股票逐只核验失败。')
    return dict(source=counts['source'], excluded=counts['excluded'], valid=counts['market'],
                gap_count=len(gaps), passed=counts['passed'], source_invalid=source_invalid,
                gaps=sorted(gaps.values(), key=lambda s: s['code']), resource_gaps=resources,
                notes=notes, derived_from_saved_report=True)
