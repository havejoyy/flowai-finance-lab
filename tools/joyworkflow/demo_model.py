"""Explicit sample-only HTTP fixture. No AI, credentials, or external network calls."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from skill_runner import sample, preview, LAYERS, DISCLAIMER
from engine import encode

MODEL = 'DEMO-FIXTURE-NOT-AI'


def fixture_output(kind='sparse'):
    if kind == 'complete':
        return complete_fixture_output()
    prepared = preview(sample(), {})
    lines = prepared['lines']
    def ref(part):
        line = next(x for x in lines if part in x['text'])
        return {'id': line['id'], 'quote': line['text']}
    missing = [
        ['缺上涨/下跌家数、成交额、炸板率、连板梯队和昨日强势股反馈'],
        ['只有1天记录，前两日未知；缺驱动、梯队及后续可验证事件'],
        ['没有具体标的的地位、成交、价格与流动性资料'],
        ['只提供持续性观察门槛，没有完整交易原计划、触发和失效条件'],
        ['缺事件、流动性、波动、集中度和计划偏离资料'],
    ]
    layers = []
    for i, name in enumerate(LAYERS):
        layers.append({'name': name, 'status': '证据不足', 'support': [],
                       'counter': ['资料缺失不能被解释为不存在风险或已否定现实条件'],
                       'missing': missing[i], 'next_checks': ['补充有日期、截止时间、来源和口径的对应材料'], 'references': []})
    layers[1].update(support=['虚构D日板块甲6家涨停；只记录1天，前两日未知；预设至少连续3天同源记录。'],
                     counter=['未达到资料完整性门槛，不能直接推断板块缺乏持续性。'],
                     references=[ref('6 家'), ref('1 天'), ref('3 天')])
    layers[3].update(support=['用户设定了至少连续3天同源记录的观察门槛。'],references=[ref('3 天')])
    return {'data_completeness': '【本地模拟响应，非AI推理】这是针对随包虚构样例的预设结果，五层资料均不完整。',
            'layers': layers, 'summary': '模拟流程仅核对：6家、1天、前两日未知。现有资料不足以判断持续性。',
            'invalidation': ['新增同源连续记录可能改变资料完整度判断'],
            'risks': ['此为固定测试响应，不验证模型推理能力；不能据此做投资决策。'], 'disclaimer': DISCLAIMER}


def complete_fixture_output():
    lines = preview(sample('complete'), {})['lines']
    def refs(*parts):
        return [{'id': line['id'], 'quote': line['text']}
                for part in parts for line in [next(x for x in lines if part in x['text'])]]
    rows = [
        (['虚构D日上涨多于下跌，成交额高于前一日。'],
         ['单日市场偏强，不足以确认持续情绪状态。'],
         ['缺完整市场连续序列及昨日强势股逐股明细'], ['补同范围连续统计与逐股记录'],
         refs('D日上涨2800家', 'D日10000亿元', '昨日强势股反馈：')),
        (['三日同范围活跃数量增加，满足本例用户自设的三天、每日至少两家涨停记录条件。'],
         ['部分成分下跌，尚不能排除整体反弹；活动公告不等于订单。'],
         ['缺跨板块同期比较和活动后新增证据'], ['保持截止时点，后续另存新记录'],
         refs('逐日记录：', '研究门槛：', 'D日梯队：', '来源C驱动：', '支持事实：')),
        (['A强于板块等权平均，但弱于对照B；成交额高于此前五日均值。'],
         ['先后同向不能证明带动性，无法据此确认唯一代表。'],
         ['缺个股公告核查和更细带动性证据'], ['核对原始公告及同时段同板块对照'],
         refs('相对表现：', '成交：', '带动性观察：', '公开信息：')),
        (['研究日志写明观察条件与重新检查条件；本例没有交易计划。'],
         ['达到自设记录门槛，不等于证明主线成立或存在交易机会。'],
         ['缺其他板块比较，研究日志原记录仍待核对'], ['保留原记录，继续核对反例；交易设想检查不适用'],
         refs('记录于D-2日', '重新检查条件：', '反例检查：', '计划偏离：')),
        (['资料明确记录公告未查询、长区间波动未知；无交易操作。'],
         ['单日成交连续不能保证未来流动性。'],
         ['缺公告、较长区间波动与极端行情记录'], ['将未查事项保留为未知；仅研究时不强求账户信息'],
         refs('事件风险：', '波动与流动性：', '集中度：', '计划偏离：')),
    ]
    layers = [dict(name=name, status='证据不足', support=row[0], counter=row[1],
                   missing=row[2], next_checks=row[3], references=row[4])
              for name, row in zip(LAYERS, rows)]
    return {'data_completeness': '【本地模拟响应，非AI推理】完整填写示例：五层均有材料，但原始明细、跨板块对照和公告核查仍有缺口。',
            'layers': layers,
            'summary': '预设示范：满足自己设定的记录条件，与足以认定主线或代表性是两件事。完整填写不等于证据充分。',
            'invalidation': ['原始统计修订、范围改变或新增对照，均需重新检查。'],
            'risks': ['全部为虚构资料与固定响应；只测试软件，不验证真实模型或投资判断。'],
            'disclaimer': DISCLAIMER}


def server(port=8768):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            try:
                if self.headers.get('Host') not in ('127.0.0.1:'+str(self.server.server_port), 'localhost:'+str(self.server.server_port)):
                    raise ValueError()
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 200000 or self.path != '/v1/chat/completions':
                    raise ValueError()
                request = json.loads(self.rfile.read(size))
                kind = next((k for k in ('sparse', 'complete') if request.get('messages') == preview(sample(k), {})['messages']), None)
                if request.get('model') != MODEL or kind is None:
                    raise ValueError()
                self.server.calls += 1
                body = {'model': MODEL, 'choices': [{'message': {'content': encode(fixture_output(kind))}}]}
                status = 200
            except Exception:
                body, status = {'error': '模拟服务只接受随包两份原始虚构示例与固定Skill，不是通用模型。'}, 400
            data = encode(body).encode()
            self.send_response(status); self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    instance = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    instance.calls = 0
    return instance
