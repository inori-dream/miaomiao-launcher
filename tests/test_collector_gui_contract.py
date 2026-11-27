"""Exercise compiled GUI helper methods without UI, elevation or real capture."""
from build.project_layout import source_file
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
HARNESS=r'''
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.Serialization;
class GuiContract {
 static void Need(bool value,string message){if(!value)throw new Exception(message);}
 static void Main(string[] args){
  var assembly=Assembly.LoadFrom(args[0]);
  var form=assembly.GetType("OverFieldLocal.LauncherForm",true);
  var instance=FormatterServices.GetUninitializedObject(form);
  var row=new Dictionary<string,object>{{"id","counter"},{"state","unknown"},{"message","统计暂不可用，不代表零"},{"messageEn","Statistics unavailable, not zero"}};
  var report=new Dictionary<string,object>{{"ok",false},{"needsElevation",false},{"checks",new object[]{row}}};
  var method=form.GetMethod("PreflightText",BindingFlags.Instance|BindingFlags.NonPublic);
  string rendered=(string)method.Invoke(instance,new object[]{report,true});
  Need(rendered.Contains(args[2]),"Wrong preflight language or unknown state");
  var backend=assembly.GetType("OverFieldLocal.Backend",true);
  var active=FormatterServices.GetUninitializedObject(backend);
  string root=Path.GetFullPath(args[1]);Directory.CreateDirectory(root);
  backend.GetField("BaseDir").SetValue(active,root+Path.DirectorySeparatorChar);
  string runtime=Path.Combine(root,"runtime");Directory.CreateDirectory(runtime);
  File.WriteAllText(Path.Combine(runtime,"capture-state.json"),"{\"active\":true,\"phase\":\"capturing\"}");
  backend.GetMethod("StopCaptureQuietly").Invoke(active,null);
  Need(File.Exists(Path.Combine(runtime,"capture-stop.flag")),"Missing cooperative stop");
  Need(File.Exists(Path.Combine(runtime,"capture-launcher-exit.flag")),"Missing startup close guard");
  string inactiveRoot=Path.Combine(root,"inactive");Directory.CreateDirectory(Path.Combine(inactiveRoot,"runtime"));
  var inactive=FormatterServices.GetUninitializedObject(backend);
  backend.GetField("BaseDir").SetValue(inactive,inactiveRoot+Path.DirectorySeparatorChar);
  File.WriteAllText(Path.Combine(inactiveRoot,"runtime","capture-state.json"),"{\"active\":false,\"phase\":\"complete\"}");
  backend.GetMethod("StopCaptureQuietly").Invoke(inactive,null);
  Need(!File.Exists(Path.Combine(inactiveRoot,"runtime","capture-stop.flag")),"Inactive capture changed");
  Console.WriteLine("PASS");
 }
}
'''

class CollectorGuiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name!='nt':raise unittest.SkipTest('Framework GUI helper fixture requires Windows')
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        source=cls.root/'GuiContract.cs';source.write_text(HARNESS,encoding='utf-8-sig')
        cls.exe=cls.root/'GuiContract.exe'
        csc=Path(os.environ.get('WINDIR',r'C:\Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        result=subprocess.run([str(csc),'/nologo','/target:exe','/reference:System.Windows.Forms.dll','/reference:System.Web.Extensions.dll','/out:'+str(cls.exe),str(source)],capture_output=True)
        if result.returncode:raise RuntimeError(result.stdout.decode('utf-8',errors='replace'))
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def check(self,name,expected):
        result=subprocess.run([str(self.exe),str(source_file(ROOT, name)),str(self.root/name),expected],capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace'))
        self.assertIn(b'PASS',result.stdout)
    def test_chinese_preflight_and_close_signal(self):self.check('OverFieldLauncher.exe','统计暂不可用')
    def test_english_preflight_and_close_signal(self):self.check('OverFieldLauncher-EN.exe','Statistics unavailable')

if __name__=='__main__':unittest.main()
