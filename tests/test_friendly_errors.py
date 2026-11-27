"""Compiled checks for Friendly error translation; no capture, no window, no network."""
from build.project_layout import source_file
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
CS = r'''
using System; using System.IO; using System.Net; using System.IO.Compression;
class FriendlyContract {
 static void Need(bool v,string m){if(!v)throw new Exception(m);}
 static string D(Exception e){return OverFieldLocal.Friendly.Describe(e,"Test action");}
 static void Main(string[] args){
  Need(!String.IsNullOrWhiteSpace(D(new UnauthorizedAccessException("Access to the path 'C:\\x' is denied."))),"unauthorized empty");
  Need(D(new UnauthorizedAccessException()).Contains("管理员")||D(new UnauthorizedAccessException()).Contains("administrator"),"unauthorized not actionable");
  Need(!String.IsNullOrWhiteSpace(D(new FileNotFoundException("Could not find file 'x'."))),"missing file empty");
  Need(!String.IsNullOrWhiteSpace(D(new IOException("The process cannot access the file"))),"io empty");
  Need(!String.IsNullOrWhiteSpace(D(new WebException("The remote name could not be resolved"))),"web empty");
  Need(!String.IsNullOrWhiteSpace(D(new TimeoutException("timed out"))),"timeout empty");
  Need(!String.IsNullOrWhiteSpace(D(new ArgumentException("bad arg"))),"argument empty");
  Need(!String.IsNullOrWhiteSpace(D(new InvalidCastException("cast"))),"cast empty");
  Need(D(new AggregateException(new IOException("inner"))).Length>0,"aggregate empty");
  Need(!D(new AggregateException(new IOException("inner"))).Contains("AggregateException"),"aggregate leaked type");
  string business=D(new InvalidOperationException("请先停止私服服务，再开始公服采集，避免流量混入私服。"));
  Need(business.Contains("私服"),"business sentence replaced");
  string unknown=D(new SystemException("weird"));
  Need(!unknown.Contains("SystemException")&&!unknown.Contains("weird"),"raw exception leaked");
  string detail=OverFieldLocal.Friendly.Detail(new IOException("outer",new IOException("inner")));
  Need(detail.Contains("outer")&&detail.Contains("inner"),"detail lost chain");
  Need(OverFieldLocal.Friendly.Detail(null)=="","null detail");
  string longDetail=OverFieldLocal.Friendly.Detail(new IOException(new string('x',900)));
  Need(longDetail.Length<700,"detail unbounded");
  string causes=OverFieldLocal.Friendly.WithCaptureCauses("测试");
  Need(causes.Split('\n').Length>=5,"capture causes missing");
  string combined=OverFieldLocal.Friendly.For("停止服务",new IOException("busy"));
  Need(combined.Contains("停止服务"),"action lost");
  string tb=OverFieldLocal.Friendly.Describe(new InvalidOperationException("Traceback (most recent call last):\n  File \"x.py\", line 1\nModuleNotFoundError: No module named 'official_launcher'"),"采集");
  Need(!tb.Contains("Traceback") && (tb.Contains("app\\") || tb.Contains("No module named")),"traceback not cleaned");
  string prof=OverFieldLocal.Friendly.Describe(new InvalidOperationException("Profile operation failed: FileNotFoundError: LoloServer/Lolo.exe is missing."),"启动");
  Need(prof.Contains("LoloServer") && prof.Contains("server-kit"),"profile error lost actionable fix");
  string rate=OverFieldLocal.Friendly.Describe(new InvalidOperationException("HTTP Error 403: rate limit exceeded"),"自动更新");
  Need(!String.IsNullOrWhiteSpace(rate) && !rate.Contains("Traceback"),"rate limit friendly message invalid");
  Console.WriteLine("PASS");
 }
}
'''


class FriendlyErrorTests(unittest.TestCase):
    """The window must never print a raw framework exception to the user."""

    @classmethod
    def setUpClass(cls):
        if os.name != 'nt':
            raise unittest.SkipTest('Framework fixture requires Windows')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        source = cls.root / 'FriendlyContract.cs'
        source.write_text(CS, encoding='utf-8-sig')
        cls.exe = cls.root / 'FriendlyContract.exe'
        csc = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        result = subprocess.run(
            [str(csc), '/nologo', '/target:exe', '/codepage:65001',
             '/reference:System.Windows.Forms.dll', '/out:' + str(cls.exe),
             str(source_file(ROOT, 'Friendly.cs')), str(source_file(ROOT, 'Strings.cs')), str(source)],
            capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stdout.decode('utf-8', errors='replace'))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_friendly_messages(self):
        result = subprocess.run([str(self.exe)], capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout.decode('utf-8', errors='replace'))
        self.assertIn(b'PASS', result.stdout)

    def test_strings_cover_new_messages(self):
        table = (source_file(ROOT, 'Strings.cs')).read_text(encoding='utf-8-sig')
        for key in ['需要管理员权限；请右键以管理员身份运行后重试。',
                    '发生未预期错误，详细原因已写入运行记录；可点「打开日志文件夹」查看。',
                    '常见原因与处理建议：']:
            self.assertIn('"' + key + '"', table, key)

    def test_no_raw_exception_message_in_window(self):
        """Only Friendly/Detail may surface exception text, never e.Message directly into the UI."""
        offenders = []
        for name in ['Launcher.cs', 'UpdaterUI.cs', 'RoomManagerForm.cs']:
            for number, line in enumerate((source_file(ROOT, name)).read_text(encoding='utf-8-sig').splitlines(), 1):
                stripped = line.strip()
                if 'Friendly.' in stripped:
                    continue
                if any(token in stripped for token in
                       ['Append(error.Message)', 'Append(e.Message)', 'Append(ex.Message)',
                        'Text=error.Message', 'Text = error.Message', 'Text=e.Message',
                        'Text=e.Message', '+ e.Message', '+ error.Message', '+ ex.Message',
                        'Show(this,error.Message', 'Show(this, ex.Message']):
                    offenders.append(f'{name}:{number}: {stripped}')
        self.assertEqual(offenders, [], 'raw exception text still reaches the window')


if __name__ == '__main__':
    unittest.main()
