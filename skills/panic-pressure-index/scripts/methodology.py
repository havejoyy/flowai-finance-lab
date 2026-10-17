"""Frozen research baseline; not an official exchange index or trading signal."""
import hashlib
import json

METHOD = {
    'version': 'CN_PANIC_V1', 'universeVersion': 'CN_A_SHSZ_NONST_120D_V1',
    'name': 'A股恐慌压力指数（试验版）', 'scope': '沪深 A 股 · 非ST · 日终',
    'factors': ['F1', 'F2', 'F3', 'F4', 'F5', 'F6'],
    'dimensions': {'intensity': ['F1', 'F2'], 'breadth': ['F3', 'F4'], 'selling': ['F5', 'F6']},
    'referenceWindow': 756, 'minimumReferences': 252, 'referenceCoverage': '0.95',
    'canonicalPlaces': 10, 'rounding': 'ROUND_HALF_UP', 'listingTradingDays': 120,
    'dailyCoverage': '0.98', 'boardCoverage': '0.95', 'historyCoverage': '0.95',
    'regimeCoverage': '0.99', 'limitCoverage': '0.99',
    'applyFactorGatesPerBoard': True,
    'formula': {
        'F1': '1 - C_t / max(C_(t-59)..C_t)',
        'F2': 'sqrt(sum(min(r_d,0)^2)/10)*sqrt(252)',
        'F3': 'count(raw_close/reference_pre_close-1 < 0)/valid_daily_count',
        'F4': 'count(comparable_close_t < min(comparable_close_(t-60)..(t-1)))/valid_61_day_count',
        'F5': 'count(close_ticks == down_limit_ticks)/valid_limited_count',
        'F6': 'count((-r)/(1-down_limit/reference_pre_close) >= 0.5)/valid_limited_count',
    },
    'limitations': ['市场行为代理指标，不是心理调查、下跌概率或底部预测。',
                    '低分不等于贪婪或低投资风险；沪深300偏向大盘。',
                    '历史回算可能包含供应商事后修订，不代表当时已知。',
                    '权重与阈值为实验参数，未验证交易预测优势。'],
}
METHOD_HASH = hashlib.sha256(json.dumps(METHOD, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
