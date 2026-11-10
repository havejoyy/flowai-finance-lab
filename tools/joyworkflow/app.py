"""Local-only server; no third party dependencies."""
import argparse
import json
import os
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from engine import Store, encode
from providers import sync_market
from contextlib import contextmanager
from screening import ScreeningStore, PRESETS, LIMITS
from screening_indicators import INDICATORS
from five_indicator_library import FIVE_INDICATORS, QUALITY_INDICATORS
from market_store import MarketStore
from report_scope import report_scope
from data_update import DataUpdates


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
    screening_store = ScreeningStore(store)
    screening_store.recover()
    data_updates = DataUpdates(store)
    data_updates.recover()

    def screen_work(item):
        try:
            screening_store.execute(item)
        finally:
            lock.release()

    def data_work(item):
        try:
            data_updates.execute(item)
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
            if parsed.path.startswith('/api/screen-'):
                try:
                    if parsed.path == '/api/screen-data-update':
                        return self.reply({'update': data_updates.get(parse_qs(parsed.query).get('id', [''])[0])})
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
                        if item.get('report'):
                            item['report']['data_scope'] = report_scope(item)
                        if parsed.path != '/api/screen-export':
                            if 'snapshot' in item:
                                item['snapshot'] = {k:v for k,v in item['snapshot'].items() if k not in ('rows','evidence')}
                            if 'report' in item: item['report'].pop('histories',None)
                            item['skill'].pop('documents',None)
                        return self.reply(item)
                    return self.reply({'error':'未知筛选入口'},404)
                except ValueError as exc:
                    return self.reply({'error':str(exc)},400)
            return self.reply({'error': 'Not found'}, 404)

        def do_POST(self):
            origin = self.headers.get('Origin')
            if (not self.valid_host() or self.headers.get('X-Workflow-Token') != token or
                origin not in (None, 'http://' + self.headers.get('Host', ''))):
                return self.reply({'error': '请求校验失败'}, 403)
            if self.path not in ('/api/screen-preflight','/api/screen-data-update','/api/screen-strategies','/api/screen-runs'):
                return self.reply({'error': 'Not found'}, 404)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size < 0 or size > 8 * 1024 * 1024:
                    return self.reply({'error': '请求最大 8MB'}, 413)
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('请求须为JSON对象')
                if self.path == '/api/screen-preflight':
                    return self.reply({'existing':screening_store.previous_screening(data)})
                if self.path == '/api/screen-runs':
                    existing = screening_store.duplicate(data)
                    if existing:
                        return self.reply({'id':existing['id'],'state':existing['state'],'deduplicated':True})
                if not lock.acquire(False):
                    return self.reply({'error': '任务执行中，请稍后重试'}, 409)
                background = False
                try:
                    if self.path == '/api/screen-data-update':
                        item = data_updates.start()
                        result = {'id': item['id'], 'state': item['state']}
                        threading.Thread(target=data_work, args=(item,), daemon=True).start()
                        background = True
                    elif self.path == '/api/screen-strategies':
                        result = screening_store.save_strategy(data)
                    elif self.path == '/api/screen-runs':
                        item, started = screening_store.start(data)
                        result = {'id':item['id'],'state':item['state'],'deduplicated':not started}
                        if started:
                            threading.Thread(target=screen_work,args=(item,),daemon=True).start()
                            background = True
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
    # CLI output is UTF-8 on Windows as well as Unix; desktop logs already are.
    for stream in (sys.stdout,sys.stderr):
        if stream is not None and hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='backslashreplace')
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
