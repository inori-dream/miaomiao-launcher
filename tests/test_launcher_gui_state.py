"""GUI state contracts using temporary assemblies; no window, game or service is started."""
from build.project_layout import source_file
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = r'''
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.Serialization;
using System.Windows.Forms;
class GuiStateContract {
 static void Need(bool value,string message){if(!value)throw new Exception(message);}
 static object NewResult(Type type,int code,string output,string error){
  var value=Activator.CreateInstance(type);
  type.GetField("Code").SetValue(value,code);type.GetField("Output").SetValue(value,output);type.GetField("Error").SetValue(value,error);
  return value;
 }
 static void Throws(MethodInfo method,object target,object[] args,string expected){
  try { method.Invoke(target,args); } catch(TargetInvocationException error) {
   if(error.InnerException!=null && error.InnerException.Message.Contains(expected))return;
   throw new Exception("Wrong failure: "+error.InnerException.Message);
  }
  throw new Exception("Expected failure containing: "+expected);
 }
 static void Set(object target,string name,object value){
  target.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic).SetValue(target,value);
 }
 static object NewForm(Assembly assembly,string root,string mode){
  var formType=assembly.GetType("OverFieldLocal.LauncherForm",true);
  var form=FormatterServices.GetUninitializedObject(formType);
  var backendType=assembly.GetType("OverFieldLocal.Backend",true);
  var backend=FormatterServices.GetUninitializedObject(backendType);
  backendType.GetField("BaseDir").SetValue(backend,root+Path.DirectorySeparatorChar);
  backendType.GetField("Mode").SetValue(backend,mode);
  Set(form,"backend",backend); Set(form,"lastCaptureZip","");
  foreach(string name in new[]{"server","gateway","proxy"})
   Set(form,name,Activator.CreateInstance(assembly.GetType("OverFieldLocal.StatusTile",true),new object[]{name}));
  foreach(string name in new[]{"start","services","stop","browse","capturePreflight","captureStart","captureStop","captureSaveZip","officialBrowse","soloMode","steamMode","hostMode","captureMode","importData","backupData","openData","firewall","roomsButton"})
   Set(form,name,Activator.CreateInstance(assembly.GetType("OverFieldLocal.ActionButton",true),new object[]{name,false}));
  Set(form,"uidBox",new NumericUpDown()); Set(form,"hostBox",new ComboBox());
  foreach(string name in new[]{"profileLabel","badge","captureHint","captureProgressHint"})Set(form,name,new Label());
  Set(form,"logs",new RichTextBox());
  foreach(string name in new[]{"rootPath","officialLauncherPath"})Set(form,name,new TextBox());
  Set(form,"captureChecklist",new ListView());
  return form;
 }
 static object Status(Type type,string mode,bool ready,bool client){
  var value=Activator.CreateInstance(type);
  type.GetProperty("HttpListening").SetValue(value,ready,null);type.GetProperty("GatewayListening").SetValue(value,ready,null);
  type.GetProperty("ProxyListening").SetValue(value,ready,null);type.GetProperty("ServerRunning").SetValue(value,ready,null);
  type.GetProperty("ProxyRunning").SetValue(value,ready,null);type.GetProperty("ProxyEnabled").SetValue(value,ready,null);
  type.GetProperty("ClientRunning").SetValue(value,client,null);type.GetProperty("Mode").SetValue(value,mode,null);
  type.GetProperty("RuntimeMode").SetValue(value,ready?mode:"",null);type.GetProperty("Target").SetValue(value,"127.0.0.1",null);
  return value;
 }
 static void Main(string[] args){
  bool english=args[1]=="EN";
  var assembly=Assembly.LoadFrom(args[0]);
  if(english){
   var loc=assembly.GetType("OverFieldLocal.Loc",true);var translate=loc.GetMethod("S",BindingFlags.Public|BindingFlags.Static);
   string translated=(string)translate.Invoke(null,new object[]{"抓包启动失败（7）：synthetic"});
   Need(translated.StartsWith("Capture failed to start (7") && translated.Contains("synthetic"),"English capture error fallback missing: "+translated);
   string shortLabel=(string)translate.Invoke(null,new object[]{"失败后不截断"});
   Need(shortLabel=="失败后不截断","Short label matched prefix unexpectedly: "+shortLabel);
  }
  var formType=assembly.GetType("OverFieldLocal.LauncherForm",true);
  var statusType=assembly.GetType("OverFieldLocal.ServiceStatus",true);
  var resultType=assembly.GetType("OverFieldLocal.Result",true);
  var success=formType.GetMethod("OperationSuccessMessage",BindingFlags.Static|BindingFlags.NonPublic);
  var ready=Status(statusType,"solo",true,true);
  var ok=NewResult(resultType,0,"Client already running","");
  string already=success.Invoke(null,new object[]{"Start",true,ok,ready,(uint)42}) as string;
  Need(already==(english?"The game is already running; current progress was kept.":"游戏已经运行，已保留当前进度。"),"Wrong already-running result");
  Throws(success,null,new object[]{"Start",true,ok,Status(statusType,"solo",false,false),(uint)42},english?"services are not ready":"服务尚未就绪");
  Throws(success,null,new object[]{"Start",true,ok,Status(statusType,"solo",true,false),(uint)42},english?"client was not detected":"未检测到游戏客户端");
  Throws(success,null,new object[]{"Stop",false,ok,ready,(uint)42},english?"still shows running services":"仍显示服务正在运行");
  var failed=NewResult(resultType,1,"","synthetic backend failure");
  Throws(success,null,new object[]{"Start",false,failed,ready,(uint)42},"synthetic backend failure");
  var empty=NewResult(resultType,0,"","");
  Throws(success,null,new object[]{"Backup",false,empty,ready,(uint)42},english?"without a backup file":"没有确认备份文件");

  var matches=formType.GetMethod("StatusMatchesMode",BindingFlags.Static|BindingFlags.NonPublic);
  Need(!(bool)matches.Invoke(null,new object[]{"steam",Status(statusType,"solo",true,false)}),"Stale mode was accepted");
  string root=Path.GetFullPath(args[2]);Directory.CreateDirectory(root);
  var backendType=assembly.GetType("OverFieldLocal.Backend",true);
  var captureBackend=FormatterServices.GetUninitializedObject(backendType);
  backendType.GetField("BaseDir").SetValue(captureBackend,root+Path.DirectorySeparatorChar);
  var captureActive=backendType.GetMethod("CaptureActive");
  Need(!(bool)captureActive.Invoke(captureBackend,null),"Missing capture state blocked private operations");
  string runtime=Path.Combine(root,"runtime");Directory.CreateDirectory(runtime);
  string statePath=Path.Combine(runtime,"capture-state.json");
  File.WriteAllText(statePath,"{\"active\":true,\"phase\":\"capturing\"}");
  Need((bool)captureActive.Invoke(captureBackend,null),"Active capture did not block private operations");
  File.WriteAllText(statePath,"not-json");
  Need((bool)captureActive.Invoke(captureBackend,null),"Unknown capture state treated as inactive");
  File.WriteAllText(statePath,"{\"active\":false,\"phase\":\"complete\"}");
  Need(!(bool)captureActive.Invoke(captureBackend,null),"Completed capture still blocked private operations");
  var form=NewForm(assembly,root,"steam");
  var badge=(Label)formType.GetField("badge",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(form);
  badge.Text="sentinel";
  var updateA11y=formType.GetMethod("UpdateDynamicAccessibility",BindingFlags.Instance|BindingFlags.NonPublic);
  updateA11y.Invoke(form,null);
  Need(badge.AccessibleDescription=="sentinel","Dynamic status is not exposed to assistive technology");
  var apply=formType.GetMethod("TryApplyStatus",BindingFlags.Instance|BindingFlags.NonPublic);
  Need(!(bool)apply.Invoke(form,new object[]{"solo",Status(statusType,"solo",true,true)}),"Previous mode repainted current mode");
  Need(badge.Text=="sentinel","Stale status changed the badge");
  Need(formType.GetField("lastStatus",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(form)==null,"Stale status cached");
  Need((bool)apply.Invoke(form,new object[]{"steam",Status(statusType,"steam",false,false)}),"Current mode status rejected");
  Need(badge.Text==(english?"○  Waiting to start":"○  等待启动"),"Not-ready state shown as ready");
  var start=(Button)formType.GetField("start",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(form);
  Need(start.Text==(english?"Start game  →":"启动游戏  →"),"Steam uses the solo start label");
  Need((bool)apply.Invoke(form,new object[]{"steam",Status(statusType,"steam",true,true)}),"Running client status rejected");
  Need(start.Text==(english?"Back to game  →":"返回游戏  →"),"Running client is not focusable");

  var setBusy=formType.GetMethod("SetBusy",BindingFlags.Instance|BindingFlags.NonPublic);
  var stop=(Button)formType.GetField("stop",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(form);
  setBusy.Invoke(form,new object[]{true,"Working",false});
  Need(!start.Enabled && !stop.Enabled,"Non-cancellable work exposed a fake cancel button");
  setBusy.Invoke(form,new object[]{true,"Working",false});
  Need(!stop.Enabled && stop.Text==(english?"Working…":"正在处理…"),"Wrong non-cancellable stop state");
  setBusy.Invoke(form,new object[]{true,"Working",true});
  Need(stop.Enabled && stop.Text==(english?"Cancel":"取消操作"),"Cancellable backend operation cannot be cancelled");
  setBusy.Invoke(form,new object[]{false,"",false});
  Need(start.Enabled && stop.Enabled && stop.Text==(english?"Stop services":"停止服务"),"Idle controls did not recover");

  var captureState=formType.GetMethod("ApplyCaptureState",BindingFlags.Instance|BindingFlags.NonPublic);
  string zip=Path.Combine(root,"account.zip");File.WriteAllText(zip,"synthetic");
  var captureStart=(Button)formType.GetField("captureStart",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(form);
  var captureStop=(Button)formType.GetField("captureStop",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(form);
  var saveZip=(Button)formType.GetField("captureSaveZip",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(form);
  captureState.Invoke(form,new object[]{new Dictionary<string,object>{{"active",true},{"accountZip",zip}}});
  Need(!captureStart.Enabled && captureStop.Enabled && !saveZip.Enabled,"Active capture controls are unsafe");
  captureState.Invoke(form,new object[]{new Dictionary<string,object>{{"active",false},{"accountZip",zip},{"incompleteStreams",1}}});
  Need(captureStart.Enabled && !captureStop.Enabled && saveZip.Enabled,"Completed capture controls did not recover");
  Throws(captureState,form,new object[]{new Dictionary<string,object>()},english?"status result is incomplete":"状态结果不完整");
  var block=formType.GetMethod("PrivateWorldBlockReason",BindingFlags.Static|BindingFlags.NonPublic);
  Need(((string)block.Invoke(null,new object[]{true})).Length>0,"Capture did not block private operations");
  Need((string)block.Invoke(null,new object[]{false})=="","Inactive capture blocked private operations");

  var buttonType=assembly.GetType("OverFieldLocal.ActionButton",true);
  var button=(Control)Activator.CreateInstance(buttonType,new object[]{"Before",false});
  Need(button.AccessibleRole==AccessibleRole.PushButton && button.AccessibleName=="Before","Button accessibility metadata missing");
  button.Text="After";Need(button.AccessibleName=="After","Button accessible name did not follow its label");
  var tile=(Control)Activator.CreateInstance(assembly.GetType("OverFieldLocal.StatusTile",true),new object[]{"Server"});
  tile.GetType().GetMethod("UpdateState").Invoke(tile,new object[]{true,"Ready"});
  Need(tile.AccessibleRole==AccessibleRole.StatusBar && tile.AccessibleDescription=="Ready","Status accessibility metadata missing");
  Console.WriteLine("PASS");
 }
}
'''


class LauncherGuiStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != 'nt':
            raise unittest.SkipTest('Framework GUI fixture requires Windows')
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        harness = cls.root / 'GuiStateContract.cs'
        harness.write_text(HARNESS, encoding='utf-8-sig')
        cls.harness = cls.root / 'GuiStateContract.exe'
        csc = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        cls.assemblies = {}
        for edition, defines in (('CN', []), ('EN', ['/define:ENGLISH'])):
            output = cls.root / f'LauncherState{edition}.exe'
            # Keep this list in step with build_launcher.ps1: a launcher built from a
            # subset fails to compile as soon as Launcher.cs references a sibling
            # source, which silently reduced this suite to zero executed tests.
            command = [str(csc), '/nologo', '/target:winexe', '/codepage:65001', '/warnaserror+',
                       '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
                       '/reference:System.Web.Extensions.dll', *defines, f'/out:{output}',
                       str(source_file(ROOT, 'Launcher.cs')), str(source_file(ROOT, 'Friendly.cs')),
                       str(source_file(ROOT, 'SeasideUI.cs')), str(source_file(ROOT, 'RoomManagerForm.cs')),
                       str(source_file(ROOT, 'Strings.cs')), str(source_file(ROOT, 'UpdaterUI.cs'))]
            result = subprocess.run(command, capture_output=True, timeout=30)
            if result.returncode:
                raise RuntimeError(result.stdout.decode('utf-8', errors='replace') +
                                   result.stderr.decode('utf-8', errors='replace'))
            cls.assemblies[edition] = output
        result = subprocess.run([str(csc), '/nologo', '/target:exe', '/codepage:65001',
                                 '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
                                 f'/out:{cls.harness}', str(harness)], capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError(result.stdout.decode('utf-8', errors='replace') +
                               result.stderr.decode('utf-8', errors='replace'))

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'temp'):
            cls.temp.cleanup()

    def check(self, edition):
        result = subprocess.run([str(self.harness), str(self.assemblies[edition]), edition,
                                 str(self.root / edition)], capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        self.assertIn(b'PASS', result.stdout)

    def test_mode_selection_and_tab_order_have_accessibility_metadata(self):
        source = source_file(ROOT, 'Launcher.cs').read_text(encoding='utf-8-sig')
        self.assertIn('button.AccessibleRole = AccessibleRole.RadioButton', source)
        self.assertIn('order[i].TabIndex = i', source)
        self.assertIn('AccessibleDescription = Loc.S("单机模式")', source)

    def test_chinese_state_results_and_accessibility(self):
        self.check('CN')

    def test_english_state_results_and_accessibility(self):
        self.check('EN')

    def test_close_and_long_operations_are_bounded_off_ui_thread(self):
        source = (source_file(ROOT, 'Launcher.cs')).read_text(encoding='utf-8-sig')
        closing = source.split('FormClosing += async delegate', 1)[1].split('void ConfigureAccessibility', 1)[0]
        self.assertIn('e.Cancel = true', closing)
        self.assertIn('await Task.Run(() => backend.Cleanup())', closing)
        self.assertIn('Interval = 3000', closing)
        self.assertNotRegex(closing, r'(?<!await Task\.Run\(\) => )backend\.Cleanup\(\);')
        self.assertIn('ReadToEndAsync()', source)
        self.assertNotIn('while (checking', source)
        self.assertIn('operationCancellable && operationCancellation != null', source)
        self.assertIn('captureStartInFlight = true', source)

    def test_only_the_cleanup_path_cancels_its_close(self):
        """An update handoff must be able to close the form, or the helper never starts."""
        source = (source_file(ROOT, 'Launcher.cs')).read_text(encoding='utf-8-sig')
        closing = source.split('FormClosing += async delegate', 1)[1].split('void ConfigureAccessibility', 1)[0]
        self.assertIn('bool needsCleanup', closing)
        # Cancel must be conditional, and it must happen after the guard is computed.
        cancel_at = closing.index('e.Cancel = true')
        guard_at = closing.index('bool needsCleanup')
        self.assertGreater(cancel_at, guard_at)
        self.assertIn('!updaterHandoff', closing)
        self.assertIn('e.CloseReason != CloseReason.WindowsShutDown', closing)
        # The early return keeps every non-cleanup reason on the default close path.
        self.assertRegex(closing, r'if \(!needsCleanup\) return;')

    def test_capture_state_refresh_releases_its_guard(self):
        """A latched guard would let the capture state be read once, ever."""
        source = (source_file(ROOT, 'Launcher.cs')).read_text(encoding='utf-8-sig')
        refresh = source.split('async Task RefreshCaptureState()', 1)[1].split('internal void ApplyCaptureState', 1)[0]
        self.assertIn('checkingCaptureState=true', refresh)
        self.assertIn('finally { checkingCaptureState=false; }', refresh)


if __name__ == '__main__':
    unittest.main()
