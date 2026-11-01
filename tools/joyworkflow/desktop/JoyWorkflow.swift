import AppKit
import WebKit

final class DesktopDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var backend: Process?
    var timer: Timer?
    var attempts = 0
    var checking = false
    var baseURL: URL?
    var root: URL!
    var logHandle: FileHandle?
    var status: NSTextField!
    var spinner: NSProgressIndicator!

    func applicationDidFinishLaunching(_ notification: Notification) {
        root = URL(fileURLWithPath: ProcessInfo.processInfo.environment["JOYWORKFLOW_DATA_DIR"] ?? UserDefaults.standard.string(forKey:"dataDirectory") ?? (NSHomeDirectory()+"/Library/Application Support/JoyWorkflow"), isDirectory:true)
        do { try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true) }
        catch { fail("无法创建数据文件夹。请检查磁盘空间与文件夹权限。\n\(root.path)");return }
        makeMenu()
        window = NSWindow(contentRect:NSRect(x:0,y:0,width:1240,height:820),styleMask:[.titled,.closable,.miniaturizable,.resizable],backing:.buffered,defer:false)
        window.title="JoyWorkflow · 本地策略研究";window.minSize=NSSize(width:860,height:620);window.center();window.delegate=self
        let configuration=WKWebViewConfiguration()
        web=WKWebView(frame:window.contentView!.bounds,configuration:configuration)
        web.autoresizingMask=[.width,.height];web.navigationDelegate=self;web.uiDelegate=self
        window.contentView!.addSubview(web)
        status=NSTextField(labelWithString:"正在启动本地数据服务…")
        status.font=NSFont.systemFont(ofSize:16);status.alignment = .center;status.frame=NSRect(x:240,y:390,width:760,height:32);status.autoresizingMask=[.minXMargin,.maxXMargin,.minYMargin,.maxYMargin]
        spinner=NSProgressIndicator(frame:NSRect(x:600,y:440,width:32,height:32));spinner.style = .spinning;spinner.autoresizingMask=[.minXMargin,.maxXMargin,.minYMargin,.maxYMargin];spinner.startAnimation(nil)
        window.contentView!.addSubview(status);window.contentView!.addSubview(spinner)
        window.makeKeyAndOrderFront(nil);NSApp.activate(ignoringOtherApps:true)
        launchBackend()
    }
    func makeMenu() {
        let bar=NSMenu();let appItem=NSMenuItem();bar.addItem(appItem)
        let menu=NSMenu();appItem.submenu=menu
        menu.addItem(withTitle:"关于 JoyWorkflow",action:#selector(about),keyEquivalent:"")
        menu.addItem(.separator())
        menu.addItem(withTitle:"切换本地数据文件夹…",action:#selector(selectDataFolder),keyEquivalent:"o")
        menu.addItem(withTitle:"打开数据文件夹",action:#selector(openData),keyEquivalent:"d")
        menu.addItem(withTitle:"查看启动日志",action:#selector(openLog),keyEquivalent:"l")
        menu.addItem(withTitle:"重新载入页面",action:#selector(reloadPage),keyEquivalent:"r")
        menu.addItem(.separator());menu.addItem(withTitle:"退出 JoyWorkflow",action:#selector(NSApplication.terminate(_:)),keyEquivalent:"q")
        let editItem=NSMenuItem();bar.addItem(editItem);let edit=NSMenu(title:"编辑");editItem.submenu=edit
        for (title,action,key) in [("撤销",Selector(("undo:")),"z"),("剪切",#selector(NSText.cut(_:)),"x"),("复制",#selector(NSText.copy(_:)),"c"),("粘贴",#selector(NSText.paste(_:)),"v"),("全选",#selector(NSText.selectAll(_:)),"a")] {edit.addItem(withTitle:title,action:action,keyEquivalent:key)}
        NSApp.mainMenu=bar
    }
    func launchBackend() {
        attempts=0
        guard let executable=Bundle.main.url(forResource:"JoyWorkflowEngine",withExtension:nil,subdirectory:"engine") else {fail("应用运行文件缺失，请重新解压完整应用。");return}
        let log=root.appendingPathComponent("desktop.log")
        if !FileManager.default.fileExists(atPath:log.path) {FileManager.default.createFile(atPath:log.path,contents:nil)}
        do {
            logHandle=try FileHandle(forWritingTo:log);try logHandle?.seekToEnd()
            let process=Process();process.executableURL=executable;process.arguments=["--data-dir",root.path,"--port","0"]
            process.standardOutput=logHandle;process.standardError=logHandle
            backend=process;try process.run()
            timer=Timer.scheduledTimer(withTimeInterval:0.25,repeats:true) {[weak self] _ in self?.checkReady()}
        } catch {fail("本地服务未能启动，请查看启动日志。\n\(error.localizedDescription)")}
    }
    func checkReady() {
        attempts += 1
        if attempts>240 {timer?.invalidate();fail("启动超过60秒。请查看日志后重试，或确认数据目录未被另一进程占用。");return}
        if checking {return}
        let file=root.appendingPathComponent("desktop-session.json")
        guard let data=try? Data(contentsOf:file),let state=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any],let urlText=state["url"] as? String,let url=URL(string:urlText),url.host=="127.0.0.1",url.scheme=="http",let instance=state["instance"] as? String else {
            if let p=backend, !p.isRunning {timer?.invalidate();fail("本地服务已退出。数据没有被删除，请查看日志确认原因。")};return
        }
        checking=true
        var request=URLRequest(url:url.appendingPathComponent("api/desktop-health"));request.timeoutInterval=2
        URLSession.shared.dataTask(with:request) {[weak self] data,_,_ in
            DispatchQueue.main.async {
                guard let self=self else{return};self.checking=false
                guard let data=data,let health=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any],health["application"] as? String=="JoyWorkflow",health["instance"] as? String==instance else{return}
                self.timer?.invalidate();self.baseURL=url;self.web.load(URLRequest(url:url.appendingPathComponent("screen")))
                self.status.isHidden=true;self.spinner.stopAnimation(nil);self.spinner.isHidden=true
            }
        }.resume()
    }
    func fail(_ message:String) {
        timer?.invalidate();spinner?.stopAnimation(nil)
        let alert=NSAlert();alert.messageText="JoyWorkflow 启动遇到问题";alert.informativeText=message
        alert.addButton(withTitle:"重试启动");alert.addButton(withTitle:"查看日志");alert.addButton(withTitle:"退出")
        let response=alert.runModal()
        if response == .alertFirstButtonReturn {if backend?.isRunning == true {backend?.terminate()};DispatchQueue.main.asyncAfter(deadline:.now()+1){self.launchBackend()}}
        else if response == .alertSecondButtonReturn {openLog()}
        else {NSApp.terminate(nil)}
    }
    func webView(_ webView:WKWebView,decidePolicyFor action:WKNavigationAction,decisionHandler:@escaping (WKNavigationActionPolicy)->Void) {
        guard let url=action.request.url else {decisionHandler(.cancel);return}
        if url.host==baseURL?.host && url.port==baseURL?.port && url.scheme=="http" {decisionHandler(.allow)}
        else {if ["https","http"].contains(url.scheme ?? "") {NSWorkspace.shared.open(url)};decisionHandler(.cancel)}
    }
    func webView(_ webView:WKWebView,createWebViewWith configuration:WKWebViewConfiguration,for action:WKNavigationAction,windowFeatures:WKWindowFeatures)->WKWebView? {
        if let url=action.request.url, ["http","https"].contains(url.scheme ?? "") {NSWorkspace.shared.open(url)};return nil
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication)->Bool{return true}
    func applicationShouldTerminate(_ sender:NSApplication)->NSApplication.TerminateReply {
        guard let base=baseURL else{return .terminateNow}
        var request=URLRequest(url:base.appendingPathComponent("api/desktop-health"));request.timeoutInterval=2
        URLSession.shared.dataTask(with:request) {data,_,_ in
            DispatchQueue.main.async {
                let state=data.flatMap{try? JSONSerialization.jsonObject(with:$0)} as? [String:Any]
                if state?["busy"] as? Bool == true {
                    let alert=NSAlert();alert.messageText="筛选仍在运行";alert.informativeText="现在退出会中断本轮任务，已保存的记录保留，下次启动可重试。";alert.addButton(withTitle:"继续运行");alert.addButton(withTitle:"退出应用")
                    NSApp.reply(toApplicationShouldTerminate:alert.runModal() == .alertSecondButtonReturn)
                } else {NSApp.reply(toApplicationShouldTerminate:true)}
            }
        }.resume()
        return .terminateLater
    }
    func applicationWillTerminate(_ notification:Notification) {
        timer?.invalidate()
        if backend?.isRunning == true {backend?.terminate()}
        try? logHandle?.close()
    }
    func applicationShouldHandleReopen(_ sender:NSApplication,hasVisibleWindows flag:Bool)->Bool {window?.makeKeyAndOrderFront(nil);return true}
    @objc func selectDataFolder(){
        let panel=NSOpenPanel();panel.canChooseDirectories=true;panel.canChooseFiles=false;panel.allowsMultipleSelection=false;panel.canCreateDirectories=true
        panel.message="选择旧版数据所在文件夹，或一个新的数据文件夹。原目录不会被删除或覆盖。"
        if panel.runModal() == .OK,let selected=panel.url {
            timer?.invalidate();if backend?.isRunning == true {backend?.terminate()};try? logHandle?.close()
            root=selected;UserDefaults.standard.set(selected.path,forKey:"dataDirectory")
            status.stringValue="正在切换本地数据文件夹…";status.isHidden=false;spinner.isHidden=false;spinner.startAnimation(nil)
            DispatchQueue.main.asyncAfter(deadline:.now()+1){self.launchBackend()}
        }
    }
    @objc func openData(){NSWorkspace.shared.open(root)}
    @objc func openLog(){NSWorkspace.shared.open(root.appendingPathComponent("desktop.log"))}
    @objc func reloadPage(){web?.reload()}
    @objc func about(){let a=NSAlert();a.messageText="JoyWorkflow 0.5.1";a.informativeText="本地策略研究工具 · 内置运行环境\n公开行情自动获取、校验与有限重试\n数据保存于：\(root.path)\n本构建适用于 Apple Silicon macOS。";a.runModal()}
}
let application=NSApplication.shared
let delegate=DesktopDelegate()
application.delegate=delegate
application.setActivationPolicy(.regular)
application.run()
