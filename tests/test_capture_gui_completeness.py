"""Compile GUI helpers into temporary assemblies; never show a window or start a service."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = r'''
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Reflection;
using System.Runtime.Serialization;
using System.Windows.Forms;
class GuiCaptureContract {
 static void Need(bool value,string message){if(!value)throw new Exception(message);}
 static Dictionary<string,object> Row(){
  return new Dictionary<string,object>{{"state","partial"},{"label","武器"},
   {"status","未见装备列表"},{"count",1},{"hint","打开武器、装备界面"}};
 }
 static void Main(string[] args){
  bool english=args[1]=="EN";
  var assembly=Assembly.LoadFrom(args[0]);
  var form=assembly.GetType("OverFieldLocal.LauncherForm",true);
  var warningMethod=form.GetMethod("CaptureIncompleteWarning",BindingFlags.Static|BindingFlags.NonPublic);
  string expected=english
   ? "A TCP capture gap or undecoded stream tail was found. The backup may be incomplete; the decoded account ZIP can still be saved."
   : "检测到抓包缺口或未解析的流尾部，备份可能不完整；仍可保存已解码账号 ZIP。";
  Need((string)warningMethod.Invoke(null,new object[]{new Dictionary<string,object>{{"incompleteStreams",1}}})==expected,"Missing gap warning");
  Need((string)warningMethod.Invoke(null,new object[]{new Dictionary<string,object>{{"incompleteStreams",0}}})=="","False gap warning");
  Need((string)warningMethod.Invoke(null,new object[]{new Dictionary<string,object>()})=="","Missing field warned");
  Need((string)warningMethod.Invoke(null,new object[]{new Dictionary<string,object>{{"incompleteStreams","bad"}}})=="","Malformed field warned");

  var instance=FormatterServices.GetUninitializedObject(form);
  var checklist=new ListView();
  var hint=new Label();
  form.GetField("captureChecklist",BindingFlags.Instance|BindingFlags.NonPublic).SetValue(instance,checklist);
  form.GetField("captureProgressHint",BindingFlags.Instance|BindingFlags.NonPublic).SetValue(instance,hint);
  var data=new Dictionary<string,object>{{"items",new object[]{Row()}},
   {"warning","检测到抓包缺口或未解析的流尾部，备份可能不完整；仍可保存已解码账号 ZIP。"},
   {"warningEn","A TCP capture gap or undecoded stream tail was found. The backup may be incomplete; the decoded account ZIP can still be saved."},
   {"updatedAt","synthetic"}};
  form.GetMethod("RenderCaptureProgress",BindingFlags.Instance|BindingFlags.NonPublic)
      .Invoke(instance,new object[]{data});
  Need(checklist.Items.Count==1,"Missing checklist row");
  Need(checklist.Items[0].SubItems[1].Text==(english?"Equipment list missing":"未见装备列表"),"Wrong partial status");
  Need(checklist.Items[0].ForeColor.ToArgb()==Color.FromArgb(168,99,38).ToArgb(),"Partial status is not highlighted");
  Need(hint.Text==expected,"Wrong rendered warning language");
  Console.WriteLine("PASS");
 }
}
'''


class CaptureGuiCompletenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != 'nt':
            raise unittest.SkipTest('Framework GUI helper fixture requires Windows')
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        harness = cls.root / 'GuiCaptureContract.cs'
        harness.write_text(HARNESS, encoding='utf-8-sig')
        cls.harness = cls.root / 'GuiCaptureContract.exe'
        csc = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        cls.assemblies = {}
        for edition, defines in (('CN', []), ('EN', ['/define:ENGLISH'])):
            output = cls.root / f'LauncherCapture{edition}.exe'
            # Same source set as build_launcher.ps1; a narrower list fails to
            # compile as soon as Launcher.cs references a sibling source.
            command = [str(csc), '/nologo', '/target:winexe', '/codepage:65001',
                       '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
                       '/reference:System.Web.Extensions.dll', *defines, f'/out:{output}',
                       str(ROOT / 'Launcher.cs'), str(ROOT / 'Friendly.cs'),
                       str(ROOT / 'SeasideUI.cs'), str(ROOT / 'RoomManagerForm.cs'),
                       str(ROOT / 'Strings.cs'), str(ROOT / 'UpdaterUI.cs')]
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
        result = subprocess.run([str(self.harness), str(self.assemblies[edition]), edition],
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        self.assertIn(b'PASS', result.stdout)

    def test_chinese_gap_warning_and_partial_equipment_row(self):
        self.check('CN')

    def test_english_gap_warning_and_partial_equipment_row(self):
        self.check('EN')


if __name__ == '__main__':
    unittest.main()
