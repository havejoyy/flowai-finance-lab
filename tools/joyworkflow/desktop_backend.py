"""Frozen desktop entrypoint. The user never needs a Python installation."""
import argparse,json,os,signal,sys,threading
from pathlib import Path
from urllib.request import build_opener,ProxyHandler
from urllib.parse import urlsplit

def existing_session(root):
    try:
        state=json.loads((root/'desktop-session.json').read_text())
        u=urlsplit(state['url'])
        if u.scheme!='http' or u.hostname!='127.0.0.1' or not u.port:return None
        with build_opener(ProxyHandler({})).open(state['url']+'/api/desktop-health',timeout=2) as response:
            health=json.load(response)
        if health['application']=='JoyWorkflow' and health['pid']==state['pid'] and health['instance']==state['instance']:return state
    except Exception:pass
    return None

def configure_tls():
    import certifi
    bundle=Path(__file__).resolve().parent/'desktop/ca-bundle.pem'
    os.environ['SSL_CERT_FILE']=str(bundle) if bundle.exists() else certifi.where()
    import ssl
    tls=ssl.create_default_context()
    if not tls.check_hostname or tls.verify_mode!=ssl.CERT_REQUIRED or not tls.get_ca_certs():raise RuntimeError('TLS certificate verification unavailable')
    return tls

def main():
    tls=configure_tls()
    import app
    from engine import Store,VERSION
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',type=Path,default=app.default_root());parser.add_argument('--port',type=int,default=0);parser.add_argument('--self-check',action='store_true');parser.add_argument('--check-output',type=Path)
    args=parser.parse_args()
    if args.self_check:
        from skill_runner import resources
        resources()
        import ssl
        result=dict(packaged=bool(getattr(sys,'frozen',False)),tls_verify_required=tls.verify_mode==ssl.CERT_REQUIRED,check_hostname=tls.check_hostname,trusted_ca_count=len(tls.get_ca_certs()),skill_resources_verified=True)
        if args.check_output:args.check_output.write_text(json.dumps(result),encoding='utf-8')
        if sys.stdout is not None:print(json.dumps(result))
        return 0
    root=args.data_dir.expanduser().resolve();root.mkdir(parents=True,exist_ok=True)
    if existing_session(root):return 0
    instance=os.urandom(16).hex();state_path=root/'desktop-session.json'
    def ready(url):
        state=dict(url=url,pid=os.getpid(),instance=instance,version=VERSION,data_dir=str(root),packaged=bool(getattr(sys,'frozen',False)))
        tmp=state_path.with_suffix('.tmp');tmp.write_text(json.dumps(state));tmp.chmod(0o600);tmp.replace(state_path)
    stopped=threading.Event();finished=threading.Event()
    def watch_stop():
        while not finished.wait(.25):
            try:
                if json.loads((root/'desktop-stop.json').read_text()).get('instance')==instance:stopped.set();return
            except (OSError,ValueError):pass
    threading.Thread(target=watch_stop,daemon=True).start()
    def terminate(*unused):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,terminate)
    try:
        with app.instance_lock(root):
            store=Store(root);store.recover();app.serve(store,args.port,False,on_ready=ready,desktop_instance=instance,shutdown_event=stopped)
    finally:
        finished.set()
        try:
            stop=root/'desktop-stop.json'
            if json.loads(stop.read_text()).get('instance')==instance:stop.unlink()
        except (OSError,ValueError):pass
        try:
            if json.loads(state_path.read_text()).get('pid')==os.getpid():state_path.unlink()
        except (OSError,ValueError):pass
    return 0
if __name__=='__main__':sys.exit(main())
