"""Build and validate a Windows x64 portable folder on Windows itself."""
import hashlib,json,os,platform,shutil,subprocess,sys,tempfile,zipfile
from pathlib import Path
from build_desktop import ASSETS
ROOT=Path(__file__).resolve().parent
from release_version import VERSION
def main():
    if sys.platform!='win32' or platform.machine().lower() not in ('amd64','x86_64'):raise SystemExit('Windows x64 原生构建环境必需；不交叉编译或伪装其他平台。')
    import certifi
    from skill_runner import resources
    resources();work=ROOT/'build/windows';work.mkdir(parents=True,exist_ok=True);out=ROOT/'dist/windows';out.mkdir(parents=True,exist_ok=True)
    intermediate=ROOT/'desktop/certificates/GeoTrustG2TLSCNRSA4096SHA2562022CA1.pem'
    openssl=shutil.which('openssl') or str(Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Git/usr/bin/openssl.exe')
    subprocess.run([openssl,'verify','-CAfile',certifi.where(),str(intermediate)],check=True)
    bundle=work/'ca-bundle.pem';bundle.write_bytes(Path(certifi.where()).read_bytes()+b'\n'+intermediate.read_bytes())
    os.environ['PYINSTALLER_CONFIG_DIR']=str(work/'cache')
    args=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed','--name','JoyWorkflow','--distpath',str(work/'frozen'),'--workpath',str(work/'pyinstaller'),'--specpath',str(work),'--add-data',str(bundle)+':desktop']
    for asset in ASSETS:args+=['--add-data',str(ROOT/asset)+':'+str(Path(asset).parent)]
    distrust=Path(__import__('sysconfig').get_path('stdlib'))/'codex-rejected-cas.pem'
    if distrust.exists():args+=['--add-data',str(distrust)+':.']
    args+=[str(ROOT/'windows_launcher.py')];subprocess.run(args,cwd=ROOT,check=True)
    package=work/'frozen/JoyWorkflow'
    with tempfile.TemporaryDirectory(prefix='joy-frozen-check-') as temp:
        check=Path(temp)/'self-check.json';data=Path(temp)/'data'
        subprocess.run([str(package/'JoyWorkflow.exe'),'--self-check','--check-output',str(check),'--data-dir',str(data)],check=True,timeout=30)
        result=json.loads(check.read_text(encoding='utf-8'));assert all(result[k] for k in ('packaged','tls_verify_required','check_hostname','skill_resources_verified'))
    (package/'使用说明.txt').write_text('JoyWorkflow '+VERSION+' · Windows x64 便携预览版\n\n完整解压后，双击 JoyWorkflow.exe。无需安装Python、数据库或Node。请保留旁边的_internal文件夹。\n启动器就绪后自动在默认浏览器打开本地研究页面；网页关闭后可点“打开研究页面”再次打开。退出启动器停止本地服务。\n数据默认位于 %LOCALAPPDATA%\\JoyWorkflow，更新应用不会覆盖资料。运行筛选时需要联网，无需行情API Key。\n自动获取、校验、有限重试；缺失项明确列出，不以示例或旧日替代。\n本包未进行Windows Authenticode签名。系统如阻止启动，请勿关闭安全保护，等待签名版本或使用经审核的源码。\n教程：https://github.com/havejoyy/flowai-finance-lab/tree/main/tools/joyworkflow\n',encoding='utf-8-sig')
    shutil.copy2(ROOT/'LICENSE',package/'LICENSE');shutil.copy2(ROOT/'NOTICE.md',package/'NOTICE.md')
    third=package/'third-party';third.mkdir(exist_ok=True)
    for name in ('LICENSE.txt','LICENSE'):
        if (Path(sys.base_prefix)/name).is_file():shutil.copy2(Path(sys.base_prefix)/name,third/'CPython-LICENSE.txt');break
    from importlib.metadata import distribution
    for name in ('pyinstaller','certifi'):
        dist=distribution(name)
        for f in dist.files or []:
            if Path(str(f)).name.upper().startswith(('LICENSE','COPYING')) and dist.locate_file(f).is_file():shutil.copy2(dist.locate_file(f),third/(name+'-'+Path(str(f)).name))
    files=[p for p in package.rglob('*') if p.is_file()]
    forbidden=[str(p.relative_to(package)) for p in files if p.suffix.lower() in ('.sqlite3','.db','.log') or p.name in ('.env','config.json','desktop-session.json')];assert not forbidden,forbidden
    archive=out/('JoyWorkflow-Windows-x64-'+VERSION+'.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in files:z.write(p,Path('JoyWorkflow-Windows')/p.relative_to(package))
    sha=hashlib.sha256(archive.read_bytes()).hexdigest();archive.with_suffix('.zip.sha256').write_text(sha+'  '+archive.name+'\n',encoding='utf-8')
    code_hashes={p.name:hashlib.sha256(p.read_text(encoding='utf-8').replace('\r\n','\n').encode('utf-8')).hexdigest() for p in ROOT.glob('*.py') if not p.name.startswith('test_')}
    manifest=dict(code_hashes_utf8_lf=code_hashes,version=VERSION,os='Windows',architecture='x64',python_runtime=sys.version.split()[0],python_required_on_user_machine=False,archive=archive.name,sha256=sha,signature='unsigned',private_data_included=False,self_check=result)
    (out/'windows-build-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(manifest,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
