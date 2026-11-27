"""Native UTF-8 fixture tests under GBK PowerShell 5.1, without using real PktMon."""
from build.project_layout import source_file
import os, subprocess, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CS=r'''
using System; using System.IO; using System.Text;
class FakePktMon {
 static int Main(string[] args){
  Console.OutputEncoding=new UTF8Encoding(false);
  string mode=Environment.GetEnvironmentVariable("FAKE_PKTMON_MODE")??"idle-cn";
  string op=String.Join(" ",args);
  if(args.Length>0 && args[0]=="echoargs"){Console.Write(args[1]+"|"+args[2]);return 0;}
  if(mode=="native-error"){Console.Error.Write("拒绝访问。需要管理员权限。");return 5;}
  if(op=="stop" || op=="filter remove"){
   File.AppendAllText(Environment.GetEnvironmentVariable("FAKE_OPERATION_LOG"),op+"\n");Console.Write("ok");return 0;
  }
  if(op=="status"){
   if(mode=="idle-cn")Console.Write("数据包监视器没有运行。\r\r\n");
   else if(mode=="idle-en")Console.Write("Packet Monitor is not running.\r\n");
   else if(mode=="busy-owned" || mode=="busy-segment" || mode=="busy-missing-etl")Console.Write("正在运行\r\n日志文件: "+Environment.GetEnvironmentVariable("FAKE_ETL")+"\r\n");
   else if(mode=="busy-other")Console.Write("正在运行\r\n日志文件: C:\\OtherDiagnostic\\capture.etl\r\n");
   else Console.Write("unrecognized monitor state");
   return 0;
  }
  if(op=="filter list"){
   if(mode=="busy-owned" || mode=="busy-segment" || mode=="busy-missing-etl")Console.Write("1 OFBackup_11001 TCP 11001\r\n");
   else if(mode=="busy-other")Console.Write("1 OtherDiagnostic TCP 11001\r\n");
   else Console.Write(mode=="idle-en"?"none":"无");return 0;
  }
  return 0;
 }
}'''
HARNESS=r'''
param([string]$CaptureScript,[string]$Fixture,[string]$Mode)
$ErrorActionPreference='Stop'
$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($CaptureScript,[ref]$null,[ref]$errors)
if($errors.Count){throw 'Capture source parse failure'}
$names=@('Invoke-PacketMonitor','Get-PacketMonitorFilterState','Remove-BackupFilters','Clear-PreviousBackupCapture')
foreach($fn in $ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $names -contains $n.Name},$true)){Invoke-Expression $fn.Extent.Text}
# Deliberately use the wrong host encoding: explicit UTF-8 readers must still work.
[Console]::OutputEncoding=[Text.Encoding]::GetEncoding(936)
$pktmon=$Fixture
if($Mode -eq 'quoted-args'){
 $path='C:\目录 with spaces\'; $tail='ends with slash\'
 $actual=Invoke-PacketMonitor @('echoargs',$path,$tail)
 if($actual -ne ($path+'|'+$tail)){throw 'Native argument quoting corrupted'}
}elseif($Mode -in @('idle-cn','idle-en','busy-owned','busy-segment','busy-missing-etl')){
 Clear-PreviousBackupCapture
}elseif($Mode -eq 'native-error'){
 $blocked=$false
 try{Clear-PreviousBackupCapture}catch{$blocked=$_.Exception.Message.Contains('拒绝访问')}
 if(-not $blocked){throw 'UTF-8 native stderr was lost'}
}else{
 $blocked=$false
 try{Clear-PreviousBackupCapture}catch{$blocked=$true}
 if(-not $blocked){throw 'Unknown/foreign capture was accepted'}
}
'PASS '+$Mode
'''

class PktMonEncodingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name!='nt':raise unittest.SkipTest('PowerShell 5.1 fixture requires Windows')
        cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name)
        source=cls.root/'Fixture.cs';source.write_text(CS,encoding='utf-8-sig')
        cls.exe=cls.root/'fake pktmon.exe'
        csc=Path(os.environ.get('WINDIR',r'C:\Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        r=subprocess.run([str(csc),'/nologo','/target:exe','/out:'+str(cls.exe),str(source)],capture_output=True)
        if r.returncode:raise RuntimeError('Fixture compile failed')
        cls.harness=cls.root/'Harness.ps1';cls.harness.write_text(HARNESS,encoding='utf-8-sig')
        cls.etl=cls.root/'account_20200101_120000_001/capture.etl';cls.etl.parent.mkdir();cls.etl.write_bytes(b'fixture')
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def check(self,mode,expected_operations,etl_override=None):
        log=self.root/(mode+'.ops')
        env={**os.environ,'FAKE_PKTMON_MODE':mode,'FAKE_OPERATION_LOG':str(log),'FAKE_ETL':str(etl_override or self.etl)}
        r=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(self.harness),
                          '-CaptureScript',str(source_file(ROOT, 'backup-tool/capture.ps1')),'-Fixture',str(self.exe),'-Mode',mode],
                         capture_output=True,env=env,timeout=30)
        self.assertEqual(r.returncode,0,r.stderr.decode('gb18030',errors='replace'))
        ops=log.read_text().splitlines() if log.exists() else []
        self.assertEqual(ops,expected_operations)
    def test_chinese_idle_not_misclassified_as_busy(self):self.check('idle-cn',[])
    def test_english_idle_not_misclassified_as_busy(self):self.check('idle-en',[])
    def test_only_owned_backup_session_is_stopped(self):self.check('busy-owned',['stop','filter remove'])
    def test_owned_segment_session_is_stopped(self):
        self.check('busy-segment',['stop','filter remove'],self.etl.with_name('segment_001.etl'))
    def test_owned_session_stopped_even_if_etl_deleted(self):
        self.check('busy-missing-etl',['stop','filter remove'],self.root/'account_20200101_120000_002/capture.etl')
    def test_foreign_session_is_never_stopped(self):self.check('busy-other',[])
    def test_ambiguous_state_is_never_stopped(self):self.check('ambiguous',[])
    def test_native_utf8_error_is_readable(self):self.check('native-error',[])
    def test_native_paths_with_spaces_and_trailing_slashes(self):self.check('quoted-args',[])

if __name__=='__main__':unittest.main()
