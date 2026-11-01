"""Local-only server; no third party dependencies."""
import argparse
import json
import os
import secrets
import sys
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from engine import Store, csv_rows, encode, timestamp, day, today
from providers import sync_market, ai_review
from skill_runner import SkillStore, resources, sample, input_material, model_info, text_report
from contextlib import contextmanager
from screening import ScreeningStore, PRESETS, LIMITS
from screening_indicators import INDICATORS
from five_indicator_library import FIVE_INDICATORS, QUALITY_INDICATORS
from market_store import MarketStore


@contextmanager
def instance_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / '.instance.lock').open('a+b')
    handle.seek(0)
    if not handle.read(1):
        handle.write(b'0'); handle.flush()
    handle.seek(0)
    try:
        if sys.platform == 'win32':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise SystemExit('该数据目录已被另一进程使用，请先关闭它。')
    try:
        yield
    finally:
        handle.close()


def default_root():
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'JoyWorkflow'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/JoyWorkflow'
    return Path.home() / '.local/share/JoyWorkflow'


def serve(store, port=8766, browser=True, on_ready=None, desktop_instance=None, shutdown_event=None):
    token = secrets.token_urlsafe(32)
    lock = threading.Lock()
    config_path = store.root / 'config.json'
    skill_store = SkillStore(store)
    skill_store.recover()
    screening_store = ScreeningStore(store)
    screening_store.recover()

    def config():
        return json.loads(config_path.read_text('utf-8')) if config_path.exists() else {}

    def run(observed):
        try:
            cfg = config()
            store.run(observed, lambda batches, date: sync_market(store, batches, date, cfg.get('tushare_token', '')))
        finally:
            lock.release()

    def skill_work(item, cfg):
        try:
            skill_store.execute(item, cfg)
        finally:
            lock.release()

    def screen_work(item):
        try:
            screening_store.execute(item)
        finally:
            lock.release()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, body, status=200, mime='application/json; charset=utf-8'):
            data = (encode(body) if mime.startswith('application/json') else body).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'nonce-" + token + "'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def valid_host(self):
            return self.headers.get('Host') in ('127.0.0.1:' + str(server.server_port), 'localhost:' + str(server.server_port))

        def do_GET(self):
            if not self.valid_host():
                return self.reply({'error': 'Host rejected'}, 403)
            parsed = urlparse(self.path)
            if parsed.path == '/api/desktop-health':
                from engine import VERSION
                return self.reply(dict(application='JoyWorkflow',version=VERSION,pid=os.getpid(),instance=desktop_instance,busy=lock.locked(),packaged=bool(getattr(sys,'frozen',False))))
            if parsed.path in ('/', '/screen'):
                html = Path(__file__).with_name('screen.html').read_text('utf-8').replace('__TOKEN__', token)
                return self.reply(html, mime='text/html; charset=utf-8')
            if parsed.path == '/observe':
                html = Path(__file__).with_name('ui.html').read_text('utf-8').replace('__TOKEN__', token)
                return self.reply(html, mime='text/html; charset=utf-8')
            if parsed.path == '/skill':
                html = Path(__file__).with_name('skill.html').read_text('utf-8').replace('__TOKEN__', token)
                return self.reply(html, mime='text/html; charset=utf-8')
            if parsed.path.startswith('/api/screen-'):
                try:
                    if parsed.path == '/api/screen-data-table':
                        params=parse_qs(parsed.query)
                        return self.reply(MarketStore(store).table(params.get('q',[''])[0],params.get('state',['all'])[0],params.get('page',['1'])[0],params.get('limit',['50'])[0],params.get('date',[''])[0],params.get('kind',['all'])[0]))
                    if parsed.path == '/api/screen-data':
                        return self.reply(MarketStore(store).status())
                    if parsed.path == '/api/screen-presets':
                        return self.reply({'presets': PRESETS, 'limits': LIMITS, 'saved': screening_store.strategies(), 'indicators': INDICATORS+FIVE_INDICATORS+QUALITY_INDICATORS})
                    if parsed.path in ('/api/screen-runs','/api/screen-export'):
                        identifier = parse_qs(parsed.query).get('id',[''])[0]
                        if not identifier: return self.reply({'runs':screening_store.list()})
                        item = screening_store.get(identifier)
                        if parsed.path != '/api/screen-export':
                            if 'snapshot' in item:
                                item['snapshot'] = {k:v for k,v in item['snapshot'].items() if k not in ('rows','evidence')}
                            if 'report' in item: item['report'].pop('histories',None)
                            item['skill'].pop('documents',None)
                        return self.reply(item)
                    return self.reply({'error':'未知筛选入口'},404)
                except ValueError as exc:
                    return self.reply({'error':str(exc)},400)
            if parsed.path.startswith('/api/skill-'):
                try:
                    if parsed.path == '/api/skill-info':
                        try:
                            skill = resources()
                            availability = {'name': skill['name'], 'hashes': skill['hashes'], 'error': ''}
                        except ValueError as exc:
                            availability = {'error': str(exc)}
                        try:
                            model = model_info(config())
                        except ValueError:
                            model = {'configured': False, 'error': '模型配置无效，请重新设置'}
                        return self.reply({'skill': availability, 'model': model, 'data_directory': str(store.root),
                                           'busy': lock.locked(), 'version': store.state()['version']})
                    if parsed.path == '/api/skill-sample':
                        return self.reply(sample(parse_qs(parsed.query).get('kind', ['sparse'])[0]))
                    if parsed.path == '/api/skill-material':
                        name = parse_qs(parsed.query).get('name', ['guide'])[0]
                        return self.reply(input_material(name), mime='text/plain; charset=utf-8')
                    if parsed.path in ('/api/skill-runs', '/api/skill-export'):
                        identifier = parse_qs(parsed.query).get('id', [''])[0]
                        if not identifier and parsed.path == '/api/skill-runs':
                            return self.reply({'runs': skill_store.list()})
                        item = skill_store.get(identifier)
                        if parse_qs(parsed.query).get('format') == ['text']:
                            return self.reply(text_report(item), mime='text/plain; charset=utf-8')
                        return self.reply(item)
                except ValueError as exc:
                    return self.reply({'error': str(exc)}, 400)
                except Exception:
                    return self.reply({'error': '读取记录失败，请检查本地数据与配置'}, 500)
            if parsed.path == '/api/state':
                return self.reply({**store.state(), 'busy': lock.locked()})
            if parsed.path == '/api/export':
                identifier = parse_qs(parsed.query).get('id', [''])[0]
                with store.connect() as db:
                    row = db.execute('SELECT body FROM evaluations WHERE id=?', (identifier,)).fetchone()
                return self.reply(json.loads(row['body']) if row else {'error': '评估不存在'}, 200 if row else 404)
            return self.reply({'error': 'Not found'}, 404)

        def do_POST(self):
            origin = self.headers.get('Origin')
            if (not self.valid_host() or self.headers.get('X-Workflow-Token') != token or
                origin not in (None, 'http://' + self.headers.get('Host', ''))):
                return self.reply({'error': '请求校验失败'}, 403)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size < 0 or size > 8 * 1024 * 1024:
                    return self.reply({'error': '请求最大 8MB'}, 413)
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('请求须为JSON对象')
                if self.path == '/api/screen-preflight':
                    return self.reply({'existing':screening_store.previous_screening(data)})
                if self.path == '/api/skill-runs' and data.get('consent') is True:
                    existing = skill_store.get(data.get('id', ''))
                    if existing['state'] != 'DRAFT':
                        return self.reply({'id': existing['id'], 'state': existing['state'], 'deduplicated': True})
                if self.path == '/api/screen-runs':
                    existing = screening_store.duplicate(data)
                    if existing:
                        return self.reply({'id':existing['id'],'state':existing['state'],'deduplicated':True})
                if not lock.acquire(False):
                    return self.reply({'error': '任务执行中，请稍后重试'}, 409)
                background = False
                try:
                    if self.path == '/api/screen-strategies':
                        result = screening_store.save_strategy(data)
                    elif self.path == '/api/screen-runs':
                        item, started = screening_store.start(data)
                        result = {'id':item['id'],'state':item['state'],'deduplicated':not started}
                        if started:
                            threading.Thread(target=screen_work,args=(item,),daemon=True).start()
                            background = True
                    elif self.path == '/api/skill-preview':
                        result = skill_store.draft(data, config())
                    elif self.path == '/api/skill-runs':
                        cfg = config()
                        item, started = skill_store.start(data.get('id', ''), cfg, data.get('consent'))
                        result = {'id': item['id'], 'state': item['state'], 'deduplicated': not started}
                        if started:
                            threading.Thread(target=skill_work, args=(item, cfg), daemon=True).start()
                            background = True
                    elif self.path == '/api/run':
                        observed = day(data.get('observed') or today())
                        if observed > today():
                            raise ValueError('不能运行未来日期')
                        threading.Thread(target=run, args=(data.get('observed'),), daemon=True).start()
                        background = True
                        result = {'message': '已启动；结果请查看运行记录'}
                    elif self.path == '/api/demo':
                        result = store.demo()
                    elif self.path == '/api/batches':
                        result = store.create_batch(data)
                    elif self.path == '/api/import-market':
                        result = store.import_market('csv', csv_rows(data['prices']), csv_rows(data['calendar']))
                    elif self.path == '/api/config':
                        old = config()
                        clearing = data.get('clear', [])
                        if not isinstance(clearing, list) or any(k not in ('tushare_token', 'llm_key', 'llm_url', 'llm_model') for k in clearing):
                            raise ValueError('清除配置项无效')
                        for key in clearing:
                            old.pop(key, None)
                        for key in ('tushare_token', 'llm_key', 'llm_url', 'llm_model'):
                            if data.get(key):
                                old[key] = str(data[key]).strip()
                        temporary = config_path.with_suffix('.tmp')
                        fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                        os.chmod(temporary, 0o600)
                        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                            stream.write(encode(old))
                        os.replace(temporary, config_path)
                        result = {'message': '配置已保存；密钥不会返回页面。留空保留旧值。'}
                    elif self.path == '/api/backup':
                        result = {'path': store.backup()}
                    elif self.path == '/api/ai':
                        if data.get('consent') is not True:
                            raise ValueError('需明确同意发送规则、候选代码与统计给模型服务商')
                        with store.connect() as db:
                            row = db.execute('SELECT body FROM evaluations WHERE id=?', (data['id'],)).fetchone()
                        if not row:
                            raise ValueError('评估不存在')
                        cfg = config()
                        content = ai_review(json.loads(row['body']), cfg)
                        with store.connect() as db:
                            db.execute('INSERT INTO ai_reports VALUES(?,?,?,?,?)', (str(uuid.uuid4()), data['id'], timestamp(), cfg.get('llm_model'), content))
                        result = {'review': content}
                    else:
                        raise ValueError('未知操作')
                    self.reply(result, 202 if background else 200)
                finally:
                    if not background:
                        lock.release()
            except (ValueError, KeyError, TypeError) as exc:
                self.reply({'error': str(exc)}, 400)
            except Exception:
                self.reply({'error': '执行失败，请检查配置、文件格式或服务权限；敏感异常已隐藏'}, 500)

    try:
        server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    except OSError:
        raise SystemExit('无法监听本地端口；请换用 --port 或停止您自己的旧实例') from None
    url = 'http://127.0.0.1:' + str(server.server_port)
    print('JoyWorkflow: ' + url + '\n数据目录: ' + str(store.root), flush=True)
    if on_ready:on_ready(url)
    if shutdown_event is not None:
        threading.Thread(target=lambda:(shutdown_event.wait(),server.shutdown()),daemon=True).start()
    if browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, default=default_root())
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--run-once', action='store_true')
    parser.add_argument('--import-batch', type=Path)
    args = parser.parse_args()
    with instance_lock(args.data_dir):
        return execute(args)


def execute(args):
    store = Store(args.data_dir)
    store.recover()
    if args.demo:
        print(encode(store.demo()))
    elif args.import_batch:
        print(encode(store.create_batch(json.loads(args.import_batch.read_text('utf-8-sig')))))
    elif args.run_once:
        path = store.root / 'config.json'
        cfg = json.loads(path.read_text('utf-8')) if path.exists() else {}
        result = store.run(sync=lambda batches, date: sync_market(store, batches, date, cfg.get('tushare_token', '')))
        print(encode(result))
        return 0 if result['status'] == 'COMPLETED' else 1
    else:
        serve(store, args.port, not args.no_browser)
    return 0


if __name__ == '__main__':
    sys.exit(main())
