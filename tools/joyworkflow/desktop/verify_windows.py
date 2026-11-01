"""Exercise the actual frozen executable, clean data and public market workflow."""
import hashlib,json,os,shutil,sqlite3,subprocess,sys,tempfile,time,uuid,zipfile
from pathlib import Path
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'dist/windows';OPENER=build_opener(ProxyHandler({}))
def read_json(path):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return None

def wait_session(folder,process,seconds=60):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if process.poll() is not None:raise AssertionError('Executable exited early: '+str(process.returncode)+' '+str(read_json(folder/'desktop-session.json')))
        state=read_json(folder/'desktop-session.json')
        if state:
            try:
                with OPENER.open(state['url']+'/api/desktop-health',timeout=2) as r:h=json.load(r)
                if h['instance']==state['instance'] and h['packaged']:return state
            except OSError:pass
        time.sleep(.2)
    raise AssertionError('No frozen service ready in deadline')
def stop(folder,state,process):
    (folder/'desktop-stop.json').write_text(json.dumps({'instance':state['instance']}),encoding='utf-8');process.wait(timeout=20)
    assert not (folder/'desktop-session.json').exists()

def main():
    assert sys.platform=='win32';archive=OUT/'JoyWorkflow-Windows-x64-0.5.1.zip';proof={'platform':sys.platform,'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'host_python_not_on_PATH':True}
    with tempfile.TemporaryDirectory(prefix='joy Windows ') as temp:
        base=Path(temp)/'中文 解压目录';base.mkdir()
        with zipfile.ZipFile(archive) as z:z.extractall(base)
        exe=base/'JoyWorkflow-Windows/JoyWorkflow.exe';env=dict(os.environ,PATH=str(Path(os.environ['SystemRoot'])/'System32')+';'+os.environ['SystemRoot'],PYINSTALLER_RESET_ENVIRONMENT='1');env.pop('PYTHONHOME',None);env.pop('PYTHONPATH',None);env.pop('PYTHONUTF8',None)
        assert shutil.which('python',path=env['PATH']) is None
        root=base/'中文 数据';check=base/'check.json'
        subprocess.run([str(exe),'--self-check','--check-output',str(check),'--data-dir',str(root)],env=env,check=True,timeout=40)
        proof['self_check']=read_json(check);assert proof['self_check']['packaged'] and proof['self_check']['tls_verify_required'] and proof['self_check']['check_hostname']
        processes=[];states=[]
        try:
            process=subprocess.Popen([str(exe),'--engine','--data-dir',str(root),'--port','0'],env=env);processes.append(process);state=wait_session(root,process);states.append((root,state));url=state['url']
            with OPENER.open(url+'/screen') as r:html=r.read().decode('utf-8')
            assert '获取数据并运行筛选' in html;token=html.split("token='")[1].split("'")[0]
            def call(path,data=None):
                raw=json.dumps(data,ensure_ascii=False).encode('utf-8') if data is not None else None
                try:
                    with OPENER.open(Request(url+'/api/'+path,data=raw,headers={'Content-Type':'application/json','X-Workflow-Token':token}),timeout=30) as r:return json.load(r)
                except HTTPError as exc:raise AssertionError(str(exc.code)+' '+exc.read().decode('utf-8')) from exc
            strategy=call('screen-strategies',dict(template='volume',name='Windows公开行情验收',params=dict(min_amount=5,min_pct=0,volume_ratio=1,max_check=10)))
            run=call('screen-runs',dict(template='volume',saved_id=strategy['id'],mode='live',request_id=str(uuid.uuid4())))
            deadline=time.monotonic()+600
            while time.monotonic()<deadline:
                result=call('screen-runs?id='+run['id'])
                if result['state']!='RUNNING':break
                time.sleep(2)
            export=call('screen-export?id='+run['id']);report=export.get('report') or {}
            proof['real_market']={k:export.get(k) for k in ('id','mode','state','message','model_used','data_state')};proof['real_market']['counts']=report.get('counts');proof['real_market']['as_of']=report.get('as_of');proof['real_market']['data_kind']=report.get('data_kind')
            # Save only an aggregate, no raw market cache or runtime session secrets.
            (OUT/'windows-verification.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
            assert export['state'] in ('PARTIAL','COMPLETED'),export['message']
            assert export['mode']=='live' and export['model_used'] is False and report['data_kind']=='real'
            assert report['counts']['market']>1000 and report['counts']['checked']>0 and report['counts']['history_failed']<report['counts']['checked']
            before=hashlib.sha256(json.dumps(export,sort_keys=True).encode()).hexdigest()
            stop(root,state,process)
            process=subprocess.Popen([str(exe),'--engine','--data-dir',str(root),'--port','0'],env=env);processes.append(process);state=wait_session(root,process);states.append((root,state));url=state['url']
            with OPENER.open(url+'/screen') as r:token=r.read().decode('utf-8').split("token='")[1].split("'")[0]
            after=call('screen-export?id='+run['id']);assert hashlib.sha256(json.dumps(after,sort_keys=True).encode()).hexdigest()==before
            proof['restart_keeps_report']=True;stop(root,state,process)
            # Launch the actual windowed controller, not python.exe. No GUI-driving hooks.
            gui_root=base/'启动器 数据';gui=subprocess.Popen([str(exe),'--data-dir',str(gui_root)],env=env);processes.append(gui)
            gui_state=wait_session(gui_root,gui);proof['windowed_controller_starts_frozen_backend']=True
            (gui_root/'desktop-stop.json').write_text(json.dumps({'instance':gui_state['instance']}),encoding='utf-8')
            deadline=time.monotonic()+20
            while (gui_root/'desktop-session.json').exists() and time.monotonic()<deadline:time.sleep(.2)
            assert not (gui_root/'desktop-session.json').exists();gui.terminate();gui.wait(timeout=10)
        finally:
            for folder,state in states:
                if (folder/'desktop-session.json').exists():(folder/'desktop-stop.json').write_text(json.dumps({'instance':state['instance']}),encoding='utf-8')
            for p in processes:
                if p.poll() is None:p.terminate();p.wait(timeout=15)
    proof['success']=True;(OUT/'windows-verification.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:proof[k] for k in ('success','restart_keeps_report','windowed_controller_starts_frozen_backend','archive_sha256')},ensure_ascii=False))
if __name__=='__main__':main()
