"""Build an Apple Silicon app with bundled CPython. No private data in artifacts."""
import hashlib,json,os,platform,plistlib,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
ASSETS=['screen.html','ui.html','skill.html','输入资料说明.md','templates/five-layer-input.txt','examples/five-layer-complete.txt','examples/five-layer.txt','skills/youzi-market-research/SKILL.md','skills/youzi-market-research/references/funnel-framework.md','skills/youzi-market-research/manifest.json','strategies/youzi-five-layer-breakout-v1.json']
def main():
    if sys.platform!='darwin' or platform.machine()!='arm64':raise SystemExit('此构建脚本仅在 Apple Silicon macOS 上构建；其他平台须原生构建验证。')
    from skill_runner import resources
    resources()
    work=ROOT/'build/desktop';work.mkdir(parents=True,exist_ok=True)
    out=ROOT/'dist/desktop';out.mkdir(parents=True,exist_ok=True)
    import certifi
    intermediate=ROOT/'desktop/certificates/GeoTrustG2TLSCNRSA4096SHA2562022CA1.pem'
    # Only the official missing intermediate, verified to an existing Mozilla root.
    subprocess.run(['openssl','verify','-CAfile',certifi.where(),str(intermediate)],check=True)
    ca_bundle=work/'ca-bundle.pem';ca_bundle.write_bytes(Path(certifi.where()).read_bytes()+b'\n'+intermediate.read_bytes())
    os.environ['PYINSTALLER_CONFIG_DIR']=str(work/'cache')
    args=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--name','JoyWorkflowEngine','--distpath',str(work/'frozen'),'--workpath',str(work/'pyinstaller'),'--specpath',str(work)]
    # Preserve the bundled runtime's SSL distrust policy as-is. Its ssl.py loads
    # this adjacent file during SSLContext construction, before the CA bundle.
    stdlib=Path(__import__('sysconfig').get_path('stdlib'))
    distrust=stdlib/'codex-rejected-cas.pem'
    if distrust.exists():args+=['--add-data',str(distrust)+':.']
    args+=['--add-data',str(ca_bundle)+':desktop']
    for asset in ASSETS:args+=['--add-data',str(ROOT/asset)+':'+str(Path(asset).parent)]
    args+=[str(ROOT/'desktop_backend.py')];subprocess.run(args,cwd=ROOT,check=True)
    subprocess.run([str(work/'frozen/JoyWorkflowEngine/JoyWorkflowEngine'),'--self-check'],check=True)
    app=out/'JoyWorkflow.app'
    if app.exists():shutil.rmtree(app) # Owned generated output only, never a user's data directory.
    mac=app/'Contents/MacOS';res=app/'Contents/Resources';mac.mkdir(parents=True);res.mkdir()
    shutil.copytree(work/'frozen/JoyWorkflowEngine',res/'engine',symlinks=True)
    cache=work/'swift-cache';cache.mkdir(exist_ok=True)
    subprocess.run(['swiftc','-swift-version','5','-O','-target','arm64-apple-macosx13.0','-module-cache-path',str(cache),str(ROOT/'desktop/JoyWorkflow.swift'),'-o',str(mac/'JoyWorkflow')],check=True)
    info=dict(CFBundleName='JoyWorkflow',CFBundleDisplayName='JoyWorkflow',CFBundleExecutable='JoyWorkflow',CFBundleIdentifier='com.flowailab.joyworkflow',CFBundlePackageType='APPL',CFBundleShortVersionString='0.5.1',CFBundleVersion='501',LSMinimumSystemVersion='13.0',NSHighResolutionCapable=True,NSAppTransportSecurity=dict(NSAllowsLocalNetworking=True),NSHumanReadableCopyright='JoyWorkflow · FlowAILab')
    (app/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    (res/'第三方组件说明.txt').write_text('CPython: Python Software Foundation License, https://www.python.org/psf/license/\nPyInstaller: GPL with bootloader exception, https://pyinstaller.org/en/stable/license.html\ncertifi CA bundle: Mozilla Public License 2.0, https://github.com/certifi/python-certifi\nApple AppKit / WebKit: system frameworks.\n', encoding='utf-8')
    (res/'使用说明.txt').write_text('JoyWorkflow 0.5.1 · macOS Apple Silicon\n\n双击 JoyWorkflow.app，无需安装Python。可拖到“应用程序”文件夹。\n本地资料保存在 ~/Library/Application Support/JoyWorkflow，替换应用不会覆盖资料。\n选策略并运行时自动联网取数、校验和有限重试；失败或缺口会显示，不使用示例替代。\n关闭应用同时停止本机服务，运行中退出的任务会在下次启动标为中断。\n菜单可打开数据文件夹与启动日志。\n本构建为本地ad-hoc签名，尚未进行Apple Developer ID签名与公证；未验证其他Mac首开体验。\n', encoding='utf-8')
    subprocess.run(['codesign','--force','--deep','--sign','-',str(app)],check=True)
    subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True)
    archive=out/'JoyWorkflow-macOS-AppleSilicon-0.5.1.zip'
    if archive.exists():archive.unlink()
    subprocess.run(['ditto','-c','-k','--sequesterRsrc','--keepParent',str(app),str(archive)],check=True)
    sha=hashlib.sha256(archive.read_bytes()).hexdigest();archive.with_suffix('.zip.sha256').write_text(sha+'  '+archive.name+'\n', encoding='utf-8')
    manifest=dict(app=app.name,archive=archive.name,sha256=sha,os='macOS',architecture='arm64',python_required_on_user_machine=False,python_runtime=sys.version.split()[0],signature='ad-hoc',notarized=False,ssl_distrust_policy_preserved=distrust.exists(),ssl_distrust_sha256=hashlib.sha256(distrust.read_bytes()).hexdigest() if distrust.exists() else None,private_data_included=False,assets=ASSETS)
    (out/'build-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n', encoding='utf-8');print(json.dumps(manifest,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
