"""Compiled GUI updater policies; no network, UAC, application or real update."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT = Path(__file__).resolve().parent.parent
CS=r'''
using System; using System.IO; using System.Reflection; using System.Collections.Generic;
class UpdaterContract {
 static void Need(bool v,string m){if(!v)throw new Exception(m);}
 static void Main(string[] args){
  var a=Assembly.LoadFrom(args[0]);var t=a.GetType("OverFieldLocal.LauncherForm",true);
  var flags=BindingFlags.Static|BindingFlags.NonPublic;
  Func<string,object[],object> call=(name,values)=>t.GetMethod(name,flags).Invoke(null,values);
  string root=Path.GetFullPath(args[1]);Directory.CreateDirectory(root);
  Need(!(bool)call("UpdaterEnvironmentAllowed",new object[]{new string[0],root}),"No baseline must disable updates");
  File.WriteAllText(Path.Combine(root,"installation-manifest.json"),"{}");
  Need((bool)call("UpdaterEnvironmentAllowed",new object[]{new string[0],root}),"Ordinary install rejected");
  foreach(string flag in new[]{"--ui-preview","--smoke-test","--verify","--test"})Need(!(bool)call("UpdaterEnvironmentAllowed",new object[]{new[]{flag},root}),"Test mode allowed");
  Need((bool)call("ValidUpdaterStage",new object[]{root,"1.4.1","CN",Path.Combine(root,"runtime","updates","staging","1.4.1-CN")}),"Valid stage rejected");
  Need(!(bool)call("ValidUpdaterStage",new object[]{root,"1.4.1","CN",Path.Combine(root,"runtime","updates","staging","1.4.1-EN")}),"Wrong edition accepted");
  var idle=new Dictionary<string,object>{{"status","idle"},{"busy",new object[0]}};
  Need((bool)call("UpdaterProbeAllowsAction",new object[]{idle,false}),"Idle rejected");
  Need(!(bool)call("UpdaterProbeAllowsAction",new object[]{new Dictionary<string,object>{{"status","idle"},{"busy",new Dictionary<string,object>()}},false}),"Empty dictionary is not a busy list");
  Need(!(bool)call("UpdaterProbeAllowsAction",new object[]{new Dictionary<string,object>{{"status","idle"}},false}),"Missing busy accepted");
  var recovery=new Dictionary<string,object>{{"status","recovery_required"},{"busy",new object[0]}};
  Need((bool)call("UpdaterProbeAllowsAction",new object[]{recovery,true}),"Recovery rejected");
  Need(!(bool)call("UpdaterProbeAllowsAction",new object[]{recovery,false}),"Recovery confused with install");
  DateTime now=DateTime.UtcNow;
  Need(!(bool)call("UpdaterActionAuthorized",new object[]{true,false,false,now,now.AddMinutes(-1)}),"Paused recovery unauthorized");
  Need((bool)call("UpdaterActionAuthorized",new object[]{true,false,true,now,now.AddMinutes(-1)}),"Manual recovery rejected");
  Need(!(bool)call("UpdaterActionAuthorized",new object[]{true,true,true,now,now.AddMinutes(1)}),"Later ignored");
  // A manual request must install a downloaded stage even while auto is paused,
  // otherwise the payload sits in staging forever.
  Need((bool)call("UpdaterActionAuthorized",new object[]{false,false,true,now,now.AddMinutes(-1)}),"Manual install blocked by pause");
  Need(!(bool)call("UpdaterActionAuthorized",new object[]{false,false,true,now,now.AddMinutes(1)}),"Later ignored for manual install");
  Need(!(bool)call("UpdaterActionAuthorized",new object[]{false,false,false,now,now.AddMinutes(-1)}),"Paused auto installed unattended");
  // Countdown must survive our own 5s status poll and only stop for real user activity.
  Func<string,object[],object> step=(name,values)=>t.GetMethod(name,flags).Invoke(null,values);
  Need((int)step("UpdaterCountdownStep",new object[]{false,true,8})==8,"Status poll reset the countdown");
  Need((int)step("UpdaterCountdownStep",new object[]{false,false,8})==7,"Countdown did not advance");
  Need((int)step("UpdaterCountdownStep",new object[]{true,false,8})==-1,"External activity did not cancel");
  Need((int)step("UpdaterCountdownStep",new object[]{false,false,1})==0,"Countdown did not reach zero");
  Need((int)step("UpdaterCountdownStep",new object[]{true,true,8})==-1,"External activity overridden by internal busy");
  int walked=10;for(int i=0;i<30 && walked>0;i++)walked=(int)step("UpdaterCountdownStep",new object[]{false,(i%5)==4,walked});
  Need(walked==0,"Countdown pinned by the 5s status poll: "+walked);
  call("WriteUpdaterSettings",new object[]{root,false});
  string settings=Path.Combine(root,"runtime","updates","ui-settings.json");
  Need(!(bool)call("ParseUpdaterAutomatic",new object[]{File.ReadAllText(settings)}),"Pause lost");
  call("WriteUpdaterSettings",new object[]{root,true});
  Need((bool)call("ParseUpdaterAutomatic",new object[]{File.ReadAllText(settings)}),"Resume lost");
  foreach(string bad in new[]{"{}","null","{\"automatic\":null}","{\"automatic\":1}","{\"automatic\":\"true\"}","bad"}){
   bool rejected=false;try{call("ParseUpdaterAutomatic",new object[]{bad});}catch(TargetInvocationException){rejected=true;}Need(rejected,"Invalid settings accepted");
  }
  Need(Directory.GetFiles(Path.GetDirectoryName(settings),"*.tmp").Length==0,"Settings temporary files leaked");
  Console.WriteLine("PASS");
 }
}
'''
class UpdaterGuiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name!='nt':raise unittest.SkipTest('Framework fixture requires Windows')
        cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name)
        source=cls.root/'UpdaterContract.cs';source.write_text(CS,encoding='utf-8-sig')
        cls.exe=cls.root/'UpdaterContract.exe'
        csc=Path(os.environ.get('WINDIR',r'C:\Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        r=subprocess.run([str(csc),'/nologo','/target:exe','/reference:System.Windows.Forms.dll','/reference:System.Web.Extensions.dll','/out:'+str(cls.exe),str(source)],capture_output=True)
        if r.returncode:raise RuntimeError(r.stdout.decode('utf-8',errors='replace'))
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def check(self,name):
        r=subprocess.run([str(self.exe),str(ROOT/name),str(self.root/(name+'-data'))],capture_output=True,timeout=20)
        self.assertEqual(r.returncode,0,r.stderr.decode('utf-8',errors='replace'))
        self.assertIn(b'PASS',r.stdout)
    def test_cn_policy(self):self.check('OverFieldLauncher.exe')
    def test_en_policy(self):self.check('OverFieldLauncher-EN.exe')
if __name__=='__main__':unittest.main()
