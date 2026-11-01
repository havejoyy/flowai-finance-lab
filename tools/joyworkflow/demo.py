"""Launch isolated local demonstration with a clearly labelled, sample-only fixture."""
import argparse
import json
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from demo_model import server, MODEL


def main():
    parser = argparse.ArgumentParser(description='本地模拟演示：预设样例响应，非AI推理，无外部模型请求')
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).parent/'local-demo-data')
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--model-port', type=int, default=8768)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    config_path = args.data_dir/'config.json'
    old = json.loads(config_path.read_text()) if config_path.exists() else {}
    if old and (old.get('llm_model') != MODEL or old.get('llm_key') or old.get('tushare_token')):
        raise SystemExit('该目录已有非模拟配置，请改用新的 --data-dir；没有覆盖配置。')
    # Lock before writing config: never reconfigure another active demo instance.
    from app import instance_lock
    with instance_lock(args.data_dir / '.demo-launcher'):
        fixture = server(args.model_port)
        threading.Thread(target=fixture.serve_forever, daemon=True).start()
        config = {'llm_model': MODEL, 'llm_url': 'http://127.0.0.1:%d/v1/chat/completions' % fixture.server_port}
        with instance_lock(args.data_dir):
            fd = os.open(str(config_path),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            os.chmod(config_path,0o600)
            with os.fdopen(fd,'w') as f:json.dump(config,f)
        child = subprocess.Popen([sys.executable, str(Path(__file__).with_name('app.py')), '--port', str(args.port),
                                  '--data-dir', str(args.data_dir.resolve()), '--no-browser'], stdout=subprocess.PIPE, text=True)
        try:
            line = child.stdout.readline().strip()
            if not line.startswith('JoyWorkflow: '):
                raise SystemExit('演示应用未启动，请检查端口或数据目录。')
            url = line.removeprefix('JoyWorkflow: ')+'/skill'
            print('DEMO_URL='+url,flush=True)
            print('模拟服务：仅预设样例响应，非AI推理。Ctrl+C停止应用及模拟服务。',flush=True)
            if not args.no_browser:webbrowser.open(url)
            child.wait()
        except KeyboardInterrupt:
            pass
        finally:
            child.terminate()
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait()
            fixture.shutdown();fixture.server_close()


if __name__=='__main__':main()
