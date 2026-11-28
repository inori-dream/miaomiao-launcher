using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net;
using System.Net.NetworkInformation;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyVersion("1.1.0.0")]
[assembly: System.Reflection.AssemblyFileVersion("1.6.1.0")]
[assembly: System.Reflection.AssemblyInformationalVersion("1.6.1")]
[assembly: System.Reflection.AssemblyTitle("开放空间私服启动器")]
[assembly: System.Reflection.AssemblyProduct("开放空间私服启动器")]

namespace OverFieldLocal
{
    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            // A capture-only elevation restart waits for the original instance
            // to finish its cleanup before acquiring the same single-instance lock.
            int elevation = Array.IndexOf(args, "--capture-after-elevation");
            if (elevation >= 0)
            {
                int parentId;
                if (elevation + 1 >= args.Length || !Int32.TryParse(args[elevation + 1], out parentId) || parentId <= 0 || parentId == Process.GetCurrentProcess().Id) return;
                try
                {
                    using (var parent = Process.GetProcessById(parentId))
                    {
                        if (!String.Equals(parent.MainModule.FileName, Application.ExecutablePath, StringComparison.OrdinalIgnoreCase) || !parent.WaitForExit(30000))
                        { MessageBox.Show(Loc.S("请先退出旧启动器，再以管理员身份重新启动。")); return; }
                    }
                }
                catch (ArgumentException) { } // The original instance already exited.
                catch (Exception) { MessageBox.Show(Loc.S("无法确认旧启动器已退出，请手动以管理员身份重启。")); return; }
            }
            bool created;
            const string instanceName = "Local\\OverFieldLocalLauncher";
            using (var instance = new Mutex(true, instanceName, out created))
            {
                if (!created && Array.IndexOf(args, "--ui-preview") < 0)
                {
                    Native.FocusLauncher();
                    return;
                }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                int previewIndex = Array.IndexOf(args, "--ui-preview");
                string previewDir = previewIndex >= 0 && previewIndex + 1 < args.Length ? args[previewIndex + 1] : null;
                Application.ThreadException += delegate(object sender, ThreadExceptionEventArgs e)
                {
                    MessageBox.Show(Loc.S("操作未完成：") + Friendly.Describe(e.Exception, ""), Loc.S("开放空间启动器"), MessageBoxButtons.OK, MessageBoxIcon.Warning);
                };
                try
                {
                    var form = new LauncherForm(args);
                    Application.Run(form);
                }
                catch (Exception error)
                {
                    if (previewDir == null) throw;
                    bool complete = Native.PreviewComplete(previewDir);
                    if (!complete) Native.WritePreviewError(previewDir, error);
                    Environment.Exit(complete ? 0 : 3);
                }
            }
        }
    }

    static class Native
    {
        internal static bool PreviewComplete(string directory)
        {
            try
            {
                return Directory.Exists(directory)
                    && Directory.GetFiles(directory, "*.png").Length > 0
                    && Directory.GetFiles(directory, "*.json").Length > 0;
            }
            catch { return false; }
        }
        internal static void WritePreviewError(string directory, Exception error)
        {
            try
            {
                Directory.CreateDirectory(directory);
                File.WriteAllText(Path.Combine(directory, "preview-error.txt"), error.ToString());
            }
            catch { }
        }

        [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr handle);
        [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr handle, int command);
        public static void FocusLauncher()
        {
            foreach (var p in Process.GetProcessesByName(Process.GetCurrentProcess().ProcessName))
                using (p) { if (p.Id != Process.GetCurrentProcess().Id && p.MainWindowHandle != IntPtr.Zero) Focus(p.MainWindowHandle); }
        }
        public static void Focus(IntPtr handle) { ShowWindow(handle, 9); SetForegroundWindow(handle); }
        public static void FocusGame(string root)
        {
            foreach (var p in Process.GetProcessesByName("launcher"))
                using (p)
                    try
                    {
                        if (String.Equals(p.MainModule.FileName, Path.Combine(root, "OverField Game", "launcher.exe"), StringComparison.OrdinalIgnoreCase) && p.MainWindowHandle != IntPtr.Zero)
                            Focus(p.MainWindowHandle);
                    }
                    catch { }
        }
    }

    static class Theme
    {
        public static Color Bg = Color.FromArgb(237, 247, 250), Panel = Color.FromArgb(252, 254, 253), Border = Color.FromArgb(198, 221, 226);
        public static Color Text = Color.FromArgb(39, 71, 90), Muted = Color.FromArgb(87, 120, 137), Accent = Color.FromArgb(49, 166, 171);
        public static Font Font(float size, FontStyle style = FontStyle.Regular) { return new Font("Microsoft YaHei UI", size, style); }
        public static GraphicsPath Round(Rectangle r, int radius)
        {
            var p = new GraphicsPath(); int d = radius * 2;
            p.AddArc(r.Left, r.Top, d, d, 180, 90); p.AddArc(r.Right - d, r.Top, d, d, 270, 90);
            p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90); p.AddArc(r.Left, r.Bottom - d, d, d, 90, 90); p.CloseFigure(); return p;
        }
    }

    class Surface : Panel
    {
        public Surface() { DoubleBuffered = true; BackColor = Theme.Panel; }
        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            using (var path = Theme.Round(new Rectangle(0, 0, Width - 1, Height - 1), 12))
            using (var brush = new SolidBrush(Theme.Panel))
            using (var pen = new Pen(Theme.Border)) { e.Graphics.FillPath(brush, path); e.Graphics.DrawPath(pen, path); }
            base.OnPaint(e);
        }
    }

    class ActionButton : Button
    {
        public bool Primary;
        bool hover;
        public ActionButton(string text, bool primary = false)
        {
            Text = text; Primary = primary; Font = Theme.Font(11, FontStyle.Bold); Cursor = Cursors.Hand;
            FlatStyle = FlatStyle.Flat; FlatAppearance.BorderSize = 0; BackColor = Theme.Bg; Dock = DockStyle.Fill;
            Margin = new Padding(0, 0, 12, 0); SetStyle(ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.AllPaintingInWmPaint, true);
            AccessibleRole = AccessibleRole.PushButton; AccessibleName = text;
        }
        protected override void OnTextChanged(EventArgs e) { AccessibleName = Text; base.OnTextChanged(e); }
        protected override void OnMouseEnter(EventArgs e) { hover = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { hover = false; Invalidate(); base.OnMouseLeave(e); }
        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(Theme.Bg); e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            Color fill = Primary ? (hover ? Color.FromArgb(63, 183, 187) : Theme.Accent) : (hover ? Color.FromArgb(226, 245, 246) : Theme.Panel);
            if (!Enabled) fill = Color.FromArgb(228, 237, 239);
            using (var path = Theme.Round(new Rectangle(0, 0, Width - 1, Height - 1), 10))
            using (var brush = new SolidBrush(fill))
            using (var pen = new Pen(Primary && Enabled ? fill : Theme.Border)) { e.Graphics.FillPath(brush, path); e.Graphics.DrawPath(pen, path); }
            TextRenderer.DrawText(e.Graphics, Text, Font, new Rectangle(12, 8, Math.Max(1, Width - 24), Math.Max(1, Height - 16)), !Enabled ? Theme.Muted : Primary ? Color.White : Theme.Text, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.SingleLine | TextFormatFlags.EndEllipsis);
            if (Focused) ControlPaint.DrawFocusRectangle(e.Graphics, new Rectangle(6, 6, Width - 12, Height - 12), Theme.Muted, fill);
        }
    }

    class StatusTile : Control
    {
        public string Caption, Detail = Loc.S("正在检查");
        public bool Online;
        public StatusTile(string caption) { Caption = caption; DoubleBuffered = true; Dock = DockStyle.Fill; BackColor = Theme.Bg; Margin = new Padding(0, 0, 12, 0); AccessibleRole = AccessibleRole.StatusBar; AccessibleName = caption; AccessibleDescription = Detail; }
        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            using (var path = Theme.Round(new Rectangle(0, 0, Width - 1, Height - 1), 10))
            using (var brush = new SolidBrush(Theme.Panel))
            using (var pen = new Pen(Theme.Border)) { e.Graphics.FillPath(brush, path); e.Graphics.DrawPath(pen, path); }
            using (var dot = new SolidBrush(Online ? Theme.Accent : Theme.Muted)) e.Graphics.FillEllipse(dot, 17, 19, 7, 7);
            using (var font = Theme.Font(10, FontStyle.Bold)) TextRenderer.DrawText(e.Graphics, Caption, font, new Rectangle(33, 10, Math.Max(1, Width - 47), 28), Theme.Text, TextFormatFlags.SingleLine | TextFormatFlags.EndEllipsis | TextFormatFlags.VerticalCenter);
            using (var font = Theme.Font(9)) TextRenderer.DrawText(e.Graphics, Detail, font, new Rectangle(17, 41, Math.Max(1, Width - 34), Math.Max(1, Height - 48)), Theme.Muted, TextFormatFlags.SingleLine | TextFormatFlags.EndEllipsis);
        }
        public void UpdateState(bool online, string detail) { Online = online; Detail = detail; AccessibleName = Caption; AccessibleDescription = detail; Invalidate(); }
    }

    class Result { public int Code; public string Output, Error; }
    class ServiceStatus
    {
        public bool HttpListening { get; set; }
        public bool GatewayListening { get; set; }
        public bool ProxyListening { get; set; }
        public bool ServerRunning { get; set; }
        public bool ProxyRunning { get; set; }
        public bool ProxyEnabled { get; set; }
        public bool ClientRunning { get; set; }
        public string Mode { get; set; }
        public string RuntimeMode { get; set; }
        public string Target { get; set; }
        public string ProfilePath { get; set; }
        public bool FirewallConfigured { get; set; }
        public bool Ready { get { return HttpListening && GatewayListening && ProxyListening && ServerRunning && ProxyRunning && ProxyEnabled; } }
    }

    class Backend
    {
        public readonly string BaseDir = AppDomain.CurrentDomain.BaseDirectory;
        public string Root = "";
        public string OfficialLauncherPath = "";
        public string Language = Loc.CurrentLocale;
        public string Channel = "auto";
        public string CaptureChannel = "auto";
        public string BilibiliRoot = "", TapTapRoot = "", SteamRoot = "";
        public string BilibiliOfficialPath = "", TapTapOfficialPath = "", SteamOfficialPath = "";
        public volatile bool CaptureStartInFlight;
        public string Python;
        public string Mode = "solo";
        public string HostAddress;
        public uint Uid = 1000000;
        public bool HasSavedLanguage;
        public string LanguageSource = "";
        // Single unified launcher supporting all 5 UI locales (zh-CN/zh-TW/en/ja/ko)
        // and Bilibili/TapTap/Steam launch & capture with one settings file.
        public static readonly string[] KnownModes = { "solo", "host", "steam" };
        public static readonly string[] KnownChannels = { "auto", "bilibili", "taptap", "steam" };
        public static string SettingsName { get { return "launcher.settings.json"; } }
        public string ProfilePath { get { return Path.Combine(BaseDir, "profiles", Mode); } }
        public string ClientExe { get { return Path.Combine(ProfilePath, "client", "launcher.exe"); } }
        internal string ControlScript;
        internal int ControlTimeoutMilliseconds = 180000;
        readonly CancellationTokenSource lifetime = new CancellationTokenSource();
        internal CancellationToken LifetimeToken { get { return lifetime.Token; } }
        public void CancelAll() { lifetime.Cancel(); }
        public string AppFile(string name)
        {
            string nested = Path.Combine(BaseDir, "app", name);
            return File.Exists(nested) ? nested : Path.Combine(BaseDir, name);
        }
        public static string NormalizeChannel(string value)
        {
            if (String.IsNullOrWhiteSpace(value)) return "auto";
            string v = value.Trim().ToLowerInvariant();
            if (Array.IndexOf(KnownChannels, v) >= 0) return v;
            return "auto";
        }
        public string GetChannelRoot(string channel)
        {
            if (channel == "bilibili") return BilibiliRoot ?? "";
            if (channel == "taptap") return TapTapRoot ?? "";
            if (channel == "steam") return SteamRoot ?? "";
            return Root ?? "";
        }
        public void SetChannelRoot(string channel, string path)
        {
            path = path ?? "";
            if (channel == "bilibili") BilibiliRoot = path;
            else if (channel == "taptap") TapTapRoot = path;
            else if (channel == "steam") SteamRoot = path;
        }
        public string GetOfficialPath(string channel)
        {
            if (channel == "bilibili") return BilibiliOfficialPath ?? "";
            if (channel == "taptap") return TapTapOfficialPath ?? "";
            if (channel == "steam") return SteamOfficialPath ?? "";
            return OfficialLauncherPath ?? "";
        }
        public void SetOfficialPath(string channel, string path)
        {
            path = path ?? "";
            if (channel == "bilibili") BilibiliOfficialPath = path;
            else if (channel == "taptap") TapTapOfficialPath = path;
            else if (channel == "steam") SteamOfficialPath = path;
        }
        public static string DetectChannel(string clientExe)
        {
            if (String.IsNullOrWhiteSpace(clientExe)) return "unknown";
            try
            {
                string dir = Directory.Exists(clientExe) ? clientExe : Path.GetDirectoryName(clientExe);
                if (String.IsNullOrWhiteSpace(dir)) return "unknown";
                string overfield = Path.Combine(dir, "OverField Game");
                if (File.Exists(Path.Combine(overfield, "launcher.exe"))) dir = overfield;
                string plugins = Path.Combine(dir, "launcher_Data", "Plugins");
                if (File.Exists(Path.Combine(plugins, "x86_64", "steam_api64.dll"))) return "steam";
                if (File.Exists(Path.Combine(plugins, "x86_64", "PCGameSDK.dll"))) return "bilibili";
                if (File.Exists(Path.Combine(plugins, "Win", "PCSDK_release", "PCSDKUI.exe"))
                    || File.Exists(Path.Combine(plugins, "x86_64", "tapsdkcore.dll"))
                    || File.Exists(Path.Combine(plugins, "x86_64", "taptap_api.dll")))
                    return "taptap";
            }
            catch { }
            return "unknown";
        }
        static bool IsUsablePython(string candidate)
        {
            if (String.IsNullOrWhiteSpace(candidate)) return false;
            if (candidate.IndexOf("\\Microsoft\\WindowsApps\\", StringComparison.OrdinalIgnoreCase) >= 0) return false;
            try
            {
                if (!File.Exists(candidate)) return false;
                return new FileInfo(candidate).Length > 16384;
            }
            catch { return false; }
        }
        static string StripPowerShell7Modules(string value)
        {
            if (String.IsNullOrEmpty(value)) return value;
            var kept = new List<string>();
            foreach (string part in value.Split(Path.PathSeparator))
            {
                if (String.IsNullOrWhiteSpace(part)) continue;
                string norm = part.Replace('/', '\\');
                if (norm.IndexOf("\\PowerShell\\7", StringComparison.OrdinalIgnoreCase) >= 0
                    || norm.IndexOf("\\PowerShell\\6", StringComparison.OrdinalIgnoreCase) >= 0)
                    continue;
                kept.Add(part);
            }
            return String.Join(Path.PathSeparator.ToString(), kept.ToArray());
        }
        static void ApplyPowerShellCompatEnv(ProcessStartInfo info, string executable)
        {
            if (info == null || String.IsNullOrEmpty(executable)) return;
            if (!String.Equals(Path.GetFileName(executable), "powershell.exe", StringComparison.OrdinalIgnoreCase)) return;
            string current = Environment.GetEnvironmentVariable("PSModulePath");
            if (!String.IsNullOrEmpty(current))
                info.EnvironmentVariables["PSModulePath"] = StripPowerShell7Modules(current);
        }
        public Backend()
        {
            ControlScript = AppFile("World-Control.ps1");
            var addresses = LanAddresses(); HostAddress = addresses.Count > 0 ? addresses[0] : "";
            Python = "";
            var path = Path.Combine(BaseDir, SettingsName);
            if (!File.Exists(path))
            {
                string legacyEnPath = Path.Combine(BaseDir, "launcher.settings-en.json");
                if (File.Exists(legacyEnPath)) path = legacyEnPath;
            }
            if (File.Exists(path))
                try
                {
                    var data = new JavaScriptSerializer().Deserialize<Dictionary<string, string>>(File.ReadAllText(path, Encoding.UTF8));
                    if (data.ContainsKey("gameRoot")) Root = data["gameRoot"];
                    if (data.ContainsKey("officialLauncherPath")) OfficialLauncherPath = data["officialLauncherPath"];
                    if (data.ContainsKey("pythonExe") && IsUsablePython(data["pythonExe"])) Python = data["pythonExe"];
                    if (data.ContainsKey("mode") && Array.IndexOf(KnownModes, data["mode"]) >= 0) Mode = data["mode"];
                    if (data.ContainsKey("hostAddress") && !String.IsNullOrWhiteSpace(data["hostAddress"])) HostAddress = data["hostAddress"];
                    if (data.ContainsKey("uid")) { uint parsed; if (UInt32.TryParse(data["uid"], out parsed) && parsed > 0) Uid = parsed; }
                    if (data.ContainsKey("languageSource")) LanguageSource = (data["languageSource"] ?? "").Trim();
                    if (data.ContainsKey("language") && !String.IsNullOrWhiteSpace(data["language"]))
                    {
                        Language = Loc.NormalizeLocale(data["language"]);
                        Loc.SetLocale(Language);
                        HasSavedLanguage = true;
                        if (String.IsNullOrEmpty(LanguageSource)) LanguageSource = "user";
                    }
                    if (data.ContainsKey("channel")) Channel = NormalizeChannel(data["channel"]);
                    if (data.ContainsKey("captureChannel")) CaptureChannel = NormalizeChannel(data["captureChannel"]);
                    if (data.ContainsKey("bilibiliRoot")) BilibiliRoot = data["bilibiliRoot"] ?? "";
                    if (data.ContainsKey("taptapRoot")) TapTapRoot = data["taptapRoot"] ?? "";
                    if (data.ContainsKey("steamRoot")) SteamRoot = data["steamRoot"] ?? "";
                    if (data.ContainsKey("bilibiliOfficialPath")) BilibiliOfficialPath = data["bilibiliOfficialPath"] ?? "";
                    if (data.ContainsKey("taptapOfficialPath")) TapTapOfficialPath = data["taptapOfficialPath"] ?? "";
                    if (data.ContainsKey("steamOfficialPath")) SteamOfficialPath = data["steamOfficialPath"] ?? "";
                }
                catch { }
            if (!String.IsNullOrWhiteSpace(Root))
            {
                string detected = DetectChannel(FindClient(Root));
                if (detected != "unknown" && String.IsNullOrWhiteSpace(GetChannelRoot(detected))) SetChannelRoot(detected, Root);
            }
            if (!String.IsNullOrWhiteSpace(OfficialLauncherPath))
            {
                string detectedOfficial = DetectChannel(OfficialLauncherPath);
                if (OfficialLauncherPath.EndsWith(".url", StringComparison.OrdinalIgnoreCase)) detectedOfficial = "steam";
                if (detectedOfficial != "unknown" && String.IsNullOrWhiteSpace(GetOfficialPath(detectedOfficial))) SetOfficialPath(detectedOfficial, OfficialLauncherPath);
            }
            string bundled = Path.Combine(BaseDir, "runtime-python", "python.exe");
            string toolsPy = Path.Combine(BaseDir, "backup-tool", "tools", "python", "python.exe");
            string repoToolsPy = Path.GetFullPath(Path.Combine(BaseDir, "..", "..", "src", "runtime", "backup-tool", "tools", "python", "python.exe"));
            if (IsUsablePython(bundled)) Python = bundled;
            if (!IsUsablePython(Python) && IsUsablePython(toolsPy)) Python = toolsPy;
            if (!IsUsablePython(Python))
            {
                string localPy = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Python");
                foreach (string ver in new[] { "Python313", "Python312", "Python311" })
                {
                    string candidate = Path.Combine(localPy, ver, "python.exe");
                    if (IsUsablePython(candidate)) { Python = candidate; break; }
                }
            }
            if (!IsUsablePython(Python))
                foreach (string folder in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'))
                {
                    if (String.IsNullOrWhiteSpace(folder)) continue;
                    string candidate = Path.Combine(folder.Trim(), "python.exe");
                    if (IsUsablePython(candidate)) { Python = candidate; break; }
                }
            if (!IsUsablePython(Python) && IsUsablePython(repoToolsPy)) Python = repoToolsPy;
            if (String.IsNullOrEmpty(Python)) Python = bundled;
        }
        public void Save()
        {
            var path = Path.Combine(BaseDir, SettingsName);
            var temp = path + ".tmp";
            var payload = new Dictionary<string, string>
            {
                { "gameRoot", Root },
                { "officialLauncherPath", OfficialLauncherPath },
                { "pythonExe", Python },
                { "mode", Mode },
                { "hostAddress", HostAddress },
                { "uid", Uid.ToString() },
                { "language", HasSavedLanguage ? Loc.CurrentLocale : "" },
                { "languageSource", LanguageSource ?? "" },
                { "channel", Channel ?? "auto" },
                { "captureChannel", CaptureChannel ?? "auto" },
                { "bilibiliRoot", BilibiliRoot ?? "" },
                { "taptapRoot", TapTapRoot ?? "" },
                { "steamRoot", SteamRoot ?? "" },
                { "bilibiliOfficialPath", BilibiliOfficialPath ?? "" },
                { "taptapOfficialPath", TapTapOfficialPath ?? "" },
                { "steamOfficialPath", SteamOfficialPath ?? "" },
            };
            File.WriteAllText(temp, new JavaScriptSerializer().Serialize(payload), new UTF8Encoding(false));
            if (File.Exists(path)) File.Replace(temp, path, null); else File.Move(temp, path);
        }
        public static List<string> LanAddresses()
        {
            var result = new List<string>();
            foreach (var adapter in NetworkInterface.GetAllNetworkInterfaces())
                if (adapter.OperationalStatus == OperationalStatus.Up)
                    foreach (var item in adapter.GetIPProperties().UnicastAddresses)
                    {
                        byte[] b = item.Address.GetAddressBytes();
                        if (b.Length == 4 && (b[0] == 10 || b[0] == 172 && b[1] >= 16 && b[1] <= 31 || b[0] == 192 && b[1] == 168)) result.Add(item.Address.ToString());
                    }
            return result;
        }
        public static string Quote(string arg)
        {
            var b = new StringBuilder("\""); int slashes = 0;
            foreach (char c in arg)
            {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') b.Append('\\', slashes * 2 + 1).Append('"');
                else b.Append('\\', slashes).Append(c);
                slashes = 0;
            }
            return b.Append('\\', slashes * 2).Append('"').ToString();
        }
        public List<string> ActiveWorldModes()
        {
            var active = new List<string>();
            string runtime = Path.Combine(BaseDir, "runtime");
            if (!Directory.Exists(runtime)) return active;
            var serializer = new JavaScriptSerializer();
            foreach (string mode in KnownModes)
            {
                string file = Path.Combine(runtime, "world-state-" + mode + ".json");
                if (!File.Exists(file)) continue;
                try
                {
                    var state = serializer.Deserialize<Dictionary<string, object>>(ReadSharedUtf8Text(file));
                    if (state != null && state.ContainsKey("Active") && Convert.ToBoolean(state["Active"]))
                        active.Add(mode);
                }
                catch { }
            }
            string legacy = Path.Combine(runtime, "world-state.json");
            if (File.Exists(legacy))
            {
                try
                {
                    var state = serializer.Deserialize<Dictionary<string, object>>(ReadSharedUtf8Text(legacy));
                    if (state != null && state.ContainsKey("Active") && Convert.ToBoolean(state["Active"]))
                    {
                        string legacyMode = state.ContainsKey("Mode") ? Convert.ToString(state["Mode"]) : Mode;
                        if (Array.IndexOf(KnownModes, legacyMode) >= 0 && !active.Contains(legacyMode))
                            active.Add(legacyMode);
                    }
                }
                catch { }
            }
            return active;
        }
        public ServiceStatus StatusForMode(string mode)
        {
            var r = RunMode(mode, "Status");
            if (r.Code != 0) throw new InvalidOperationException(r.Error.Length > 0 ? r.Error : Loc.S("无法读取服务状态。"));
            return new JavaScriptSerializer().Deserialize<ServiceStatus>(r.Output);
        }
        public Result Run(string action, bool launch = false, CancellationToken cancellation = default(CancellationToken))
        {
            return RunMode(Mode, action, launch, cancellation);
        }
        public Result RunMode(string mode, string action, bool launch = false, CancellationToken cancellation = default(CancellationToken))
        {
            string targetMode = String.IsNullOrWhiteSpace(mode) ? Mode : mode;
            var arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File " + Quote(ControlScript) + " -Action " + action + " -GameRoot " + Quote(Root);
            arguments += " -Mode " + targetMode + " -HostAddress " + Quote(targetMode != "host" ? "127.0.0.1" : HostAddress) + " -Uid " + Uid.ToString();
            if (action == "Status") arguments += " -Json";
            if (action == "Start" || action == "Backup") arguments += " -PythonExe " + Quote(Python);
            if (action == "Stop") arguments += " -StopServer";
            if (launch) arguments += " -LaunchClient";
            string operations = Path.Combine(BaseDir, "runtime", "operations");
            Directory.CreateDirectory(operations);
            string resultPath = Path.Combine(operations, Guid.NewGuid().ToString("N") + ".json");
            arguments += " -ResultPath " + Quote(resultPath);
            try
            {
                ExecuteProcess(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell", "v1.0", "powershell.exe"), arguments, action == "Status" ? 12000 : ControlTimeoutMilliseconds, false, cancellation);
                if (!File.Exists(resultPath)) throw new InvalidOperationException(Loc.S("服务控制程序未返回结果，请查看运行记录。"));
                var result = new JavaScriptSerializer().Deserialize<Result>(File.ReadAllText(resultPath, Encoding.UTF8));
                if (result == null) throw new InvalidOperationException(Loc.S("服务控制程序未返回有效结果，请查看运行记录。"));
                result.Output = result.Output ?? ""; result.Error = result.Error ?? "";
                return result;
            }
            finally { try { if (File.Exists(resultPath)) File.Delete(resultPath); } catch { } }
        }
        // An in-flight capture is a detached worker too. Ask it to finish and leave
        // it to flush its own trace; never block exit waiting for a long decode.
        public bool CaptureActive()
        {
            if(CaptureStartInFlight) return true;
            try
            {
                string statePath=Path.Combine(BaseDir,"runtime","capture-state.json");
                if(!File.Exists(statePath)) return false;
                var state=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(statePath,Encoding.UTF8));
                if(state==null) return true;
                bool active=state.ContainsKey("active") && Convert.ToBoolean(state["active"]);
                string phase=state.ContainsKey("phase")?Convert.ToString(state["phase"]):"";
                return active || phase=="starting" || phase=="stopping";
            }
            catch { return true; } // Unknown is not an inactive capture.
        }
        public void StopCaptureQuietly()
        {
            try
            {
                string runtime=Path.Combine(BaseDir,"runtime");
                bool active=CaptureStartInFlight;
                string statePath=Path.Combine(runtime,"capture-state.json");
                if(File.Exists(statePath)) {
                    try {
                    var state=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(statePath,Encoding.UTF8));
                    if(state!=null) {
                        active=active || (state.ContainsKey("active") && Convert.ToBoolean(state["active"]));
                        string phase=state.ContainsKey("phase")?Convert.ToString(state["phase"]):"";
                        active=active || phase=="starting" || phase=="stopping";
                    }
                    } catch { active=true; } // Unknown is not an inactive capture.
                }
                if(!active)return;
                Directory.CreateDirectory(runtime);
                File.WriteAllText(Path.Combine(runtime,"capture-launcher-exit.flag"),"stop",new UTF8Encoding(false));
                File.WriteAllText(Path.Combine(runtime,"capture-stop.flag"),"stop",new UTF8Encoding(false));
                // The detached worker owns flush/cleanup/export. No cancelled
                // status subprocess, PID kill, or long wait is needed at exit.
            }
            catch { }
        }
        // Services are started as detached background processes, so closing the
        // launcher leaves them running unless they are stopped explicitly. This
        // bypasses lifetime (already cancelled at exit) and never throws.
        public string Cleanup()
        {
            if (!File.Exists(ControlScript)) return "";
            var arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File " + Quote(ControlScript)
                + " -Action Cleanup -GameRoot " + Quote(Root) + " -Mode " + Mode
                + " -HostAddress " + Quote(Mode != "host" ? "127.0.0.1" : HostAddress) + " -Uid " + Uid.ToString();
            try
            {
                using (var process = new Process())
                {
                    string psExe = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell", "v1.0", "powershell.exe");
                    process.StartInfo = new ProcessStartInfo(psExe, arguments)
                    { UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = BaseDir,
                      RedirectStandardOutput = true, RedirectStandardError = true,
                      StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8 };
                    ApplyPowerShellCompatEnv(process.StartInfo, psExe);
                    process.Start();
                    // Drain both pipes asynchronously: reading one to EOF before the
                    // other can deadlock and make window close unbounded.
                    var output = process.StandardOutput.ReadToEndAsync();
                    var error = process.StandardError.ReadToEndAsync();
                    if (!process.WaitForExit(20000)) { try { process.Kill(); } catch { } return ""; }
                    if (!Task.WaitAll(new Task[] { output, error }, 2000)) return "";
                    return output.Result;
                }
            }
            catch { return ""; }
        }
        public Result Execute(string executable, string arguments)
        {
            return ExecuteProcess(executable, arguments, 15000, true, CancellationToken.None);
        }
        Result ExecuteProcess(string executable, string arguments, int timeout, bool capture, CancellationToken cancellation)
        {
            using (var linked = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token, cancellation))
            using (var p = new Process())
            {
                linked.Token.ThrowIfCancellationRequested();
                p.StartInfo = new ProcessStartInfo(executable, arguments) { UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = BaseDir,
                    RedirectStandardOutput = capture, RedirectStandardError = capture };
                ApplyPowerShellCompatEnv(p.StartInfo, executable);
                if (capture) { p.StartInfo.StandardOutputEncoding = Encoding.UTF8; p.StartInfo.StandardErrorEncoding = Encoding.UTF8; }
                linked.Token.ThrowIfCancellationRequested();
                p.Start();
                var output = capture ? p.StandardOutput.ReadToEndAsync() : Task.FromResult("");
                var error = capture ? p.StandardError.ReadToEndAsync() : Task.FromResult("");
                using (linked.Token.Register(delegate { try { if (!p.HasExited) p.Kill(); } catch { } }))
                {
                    var clock = Stopwatch.StartNew();
                    while (!p.WaitForExit(100))
                    {
                        linked.Token.ThrowIfCancellationRequested();
                        if (clock.ElapsedMilliseconds > timeout)
                        {
                            try { p.Kill(); } catch { }
                            throw new TimeoutException(Loc.S("操作超时，已停止等待。请检查状态后重试。"));
                        }
                    }
                    linked.Token.ThrowIfCancellationRequested();
                    if (capture && !Task.WaitAll(new Task[] { output, error }, 2000))
                        throw new TimeoutException(Loc.S("程序已经退出，但输出流未结束，已停止等待。"));
                    return new Result { Code = p.ExitCode, Output = output.Result.Trim(), Error = error.Result.Trim() };
                }
            }
        }
        public ServiceStatus Status()
        {
            var r = Run("Status");
            if (r.Code != 0) throw new InvalidOperationException(r.Error.Length > 0 ? r.Error : Loc.S("无法读取服务状态。"));
            return new JavaScriptSerializer().Deserialize<ServiceStatus>(r.Output);
        }
        public static string FindClient(string root)
        {
            if (String.IsNullOrWhiteSpace(root)) return null;
            if (File.Exists(root) && root.EndsWith(".exe", StringComparison.OrdinalIgnoreCase)) return root;
            var direct = new[] { Path.Combine(root, "OverField Game", "launcher.exe"), Path.Combine(root, "launcher.exe") };
            foreach (var candidate in direct) if (File.Exists(candidate)) return candidate;
            try
            {
                foreach (var child in Directory.GetDirectories(root))
                    foreach (var candidate in new[] { Path.Combine(child, "OverField Game", "launcher.exe"), Path.Combine(child, "launcher.exe") })
                        if (File.Exists(candidate)) return candidate;
            }
            catch { }
            return null;
        }
        public static string FindServer(string root)
        {
            if (!String.IsNullOrWhiteSpace(root))
            {
                if (File.Exists(Path.Combine(root, "LoloServer", "Lolo.exe"))) return Path.Combine(root, "LoloServer");
                try
                {
                    foreach (var child in Directory.GetDirectories(root))
                    {
                        var nested = Path.Combine(child, "LoloServer");
                        if (File.Exists(Path.Combine(nested, "Lolo.exe"))) return nested;
                    }
                }
                catch { }
            }
            return Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "server-kit");
        }
        public static string UnsupportedChannel(string clientExe)
        {
            // Packages that authenticate through their own SDK never load PCGameSDK.dll,
            // so swapping that DLL cannot move them to a private server.
            if (String.IsNullOrEmpty(clientExe)) return null;
            var plugins = Path.Combine(Path.GetDirectoryName(clientExe), "launcher_Data", "Plugins", "x86_64");
            // QuickSDK PC is a separate local adapter; TapTap DLL presence alone
            // does not identify the active channel (official QuickSDK also ships them).
            if (File.Exists(Path.Combine(Path.GetDirectoryName(clientExe), "launcher_Data", "Plugins", "Win", "PCSDK_release", "PCSDKUI.exe"))) return null;
            if (File.Exists(Path.Combine(plugins, "PCGameSDK.dll"))) return null;
            if (File.Exists(Path.Combine(plugins, "tapsdkcore.dll")) || File.Exists(Path.Combine(plugins, "taptap_api.dll")))
                return "TapTap";
            return "Unknown";
        }
        public void ValidateRoot(string root)
        {
            string server = FindServer(root);
            foreach (var path in new[] { Path.Combine(server, "Lolo.exe"), Path.Combine(server, "config.json") })
                if (!File.Exists(path))
                    throw new InvalidOperationException(Loc.S("所选目录缺少服务端：") + path + Loc.S("\n请选择包含 LoloServer 的上层目录，或把服务端放到启动器旁边的 server-kit 文件夹。"));
            string client = FindClient(root);
            string channel = UnsupportedChannel(client);
            if (channel != null)
                throw new InvalidOperationException(
                    Loc.S("检测到 ") + channel + Loc.S(" SDK，但未找到已验证的 QuickSDK PC 接入布局。\n")
                    + Loc.S("此版本尚未适配，不能仅凭 DLL 名称推断登录方式。\n")
                    + Loc.S("请使用已验证的 QuickSDK PC 或 PCGameSDK 客户端版本，并查看分支兼容说明。"));
        }
        static string ExtractJsonError(Result result, string fallback)
        {
            string output = result.Output ?? "";
            string err = result.Error ?? "";
            string primary = !String.IsNullOrWhiteSpace(output) ? output : err;
            string message = err.Length > 0 ? err : output;
            try
            {
                var parsed = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(primary);
                if (parsed != null)
                {
                    if (parsed.ContainsKey("error")) message = Convert.ToString(parsed["error"]);
                    if (Loc.CurrentLocale == "en" && parsed.ContainsKey("errorEn")) message = Convert.ToString(parsed["errorEn"]);
                }
            }
            catch { }
            message = Loc.S(message);
            return !String.IsNullOrWhiteSpace(message) ? message : fallback;
        }
        static string ReadSharedUtf8Text(string path)
        {
            for (int attempt = 0; attempt < 4; attempt++)
            {
                try
                {
                    using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                    using (var reader = new StreamReader(stream, new UTF8Encoding(false), true))
                        return reader.ReadToEnd();
                }
                catch (IOException)
                {
                    if (attempt == 3) throw;
                    Thread.Sleep(15);
                }
            }
            return "";
        }
        bool IsUnderCaptureSessions(string target)
        {
            if (String.IsNullOrWhiteSpace(target)) return false;
            try
            {
                string sessions = Path.GetFullPath(Path.Combine(BaseDir, "runtime", "capture-sessions"));
                string prefix = sessions.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
                string resolved = Path.GetFullPath(target).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
                return resolved.StartsWith(prefix, StringComparison.OrdinalIgnoreCase);
            }
            catch { return false; }
        }
        static string FileSignature(string path)
        {
            var info = new FileInfo(path);
            return path + "|" + info.LastWriteTimeUtc.Ticks.ToString() + "|" + info.Length.ToString();
        }
        public bool TryReadCaptureProgressDirect(string previousSourceKey, out Dictionary<string, object> data, out string sourceKey, out bool waitingForInitialFile, out bool unchanged)
        {
            data = null;
            sourceKey = previousSourceKey ?? "";
            waitingForInitialFile = false;
            unchanged = false;
            try
            {
                string statePath = Path.Combine(BaseDir, "runtime", "capture-state.json");
                if (!File.Exists(statePath)) return false;
                var serializer = new JavaScriptSerializer();
                var state = serializer.Deserialize<Dictionary<string, object>>(ReadSharedUtf8Text(statePath));
                if (state == null) return false;
                string phase = state.ContainsKey("phase") ? Convert.ToString(state["phase"]) : "";
                bool active = state.ContainsKey("active") && Convert.ToBoolean(state["active"]);
                string exportDir = state.ContainsKey("export") ? Convert.ToString(state["export"]) : "";
                string captureDir = state.ContainsKey("capturePath") ? Convert.ToString(state["capturePath"]) : "";
                if (phase == "complete" && IsUnderCaptureSessions(exportDir))
                {
                    string cached = Path.Combine(Path.GetFullPath(exportDir), "capture-checklist.json");
                    if (File.Exists(cached))
                    {
                        string nextKey = FileSignature(cached);
                        if (nextKey == sourceKey) { unchanged = true; return true; }
                        var parsed = serializer.Deserialize<Dictionary<string, object>>(ReadSharedUtf8Text(cached));
                        if (parsed != null && parsed.ContainsKey("items"))
                        {
                            data = parsed;
                            sourceKey = nextKey;
                            return true;
                        }
                    }
                    return false;
                }
                if (IsUnderCaptureSessions(captureDir))
                {
                    string livePath = Path.Combine(Path.GetFullPath(captureDir), "live-progress", "progress.json");
                    if (File.Exists(livePath))
                    {
                        string nextKey = FileSignature(livePath);
                        if (nextKey == sourceKey) { unchanged = true; return true; }
                        var parsed = serializer.Deserialize<Dictionary<string, object>>(ReadSharedUtf8Text(livePath));
                        if (parsed != null && parsed.ContainsKey("items"))
                        {
                            data = parsed;
                            sourceKey = nextKey;
                            return true;
                        }
                    }
                    if (active || phase == "capturing" || phase == "starting")
                    {
                        waitingForInitialFile = true;
                        sourceKey = "";
                        return true;
                    }
                }
                if (!active && phase == "idle")
                {
                    waitingForInitialFile = true;
                    sourceKey = "";
                    return true;
                }
                return false;
            }
            catch { return false; }
        }
        public Dictionary<string, object> Official(string action, string extra = "")
        {
            string script = AppFile("official_capture.py");
            if (!File.Exists(script)) throw new InvalidOperationException(Loc.S("缺少 official_capture.py，请重新解压完整工具包。"));
            if(action == "start") extra += " --launcher " + Quote(OfficialLauncherPath) + " --launcher-managed";
            var arguments = "-X utf8 " + Quote(script) + " " + action + extra;
            int timeout = action == "stop" ? 1900000 : action == "start" ? 180000 : action == "preflight" ? 65000 : action == "export-zip" ? 120000 : 15000;
            var result = ExecuteProcess(Python, arguments, timeout, true, CancellationToken.None);
            if (result.Code != 0) throw new InvalidOperationException(ExtractJsonError(result, Loc.S("公服采集操作失败。")));
            return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(result.Output);
        }
        public Dictionary<string, object> OfficialImport(string action, string accountZip, uint uid, bool replace, bool allowIncomplete = false, bool allowOverwrite = false)
        {
            string script = AppFile("official_import.py");
            if (!File.Exists(script)) throw new InvalidOperationException(Loc.S("缺少 official_import.py，请重新解压完整工具包。"));
            var arguments = "-X utf8 " + Quote(script) + " " + action + " --file " + Quote(accountZip) + " --database " + Quote(Path.Combine(ProfilePath, "db", "lolo.db"));
            if (uid > 0) arguments += " --uid " + uid;
            if (replace) arguments += " --replace";
            if (allowIncomplete) arguments += " --allow-incomplete";
            if (allowOverwrite) arguments += " --allow-overwrite";
            var result = Execute(Python, arguments);
            if (result.Code != 0) throw new InvalidOperationException(ExtractJsonError(result, Loc.S("账号入库失败。")));
            return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(result.Output);
        }
        public Dictionary<string, object> Capture(string action, string path, uint uid, bool replace, bool allowOverwrite = false)
        {
            string arguments = "-X utf8 " + Quote(AppFile("capture_import.py")) + " " + action + " --file " + Quote(path) + " --profile " + Quote(ProfilePath) + " --uid " + uid.ToString();
            if (replace) arguments += " --replace";
            if (allowOverwrite) arguments += " --allow-overwrite";
            var result = Execute(Python, arguments);
            if (result.Code != 0) throw new InvalidOperationException(ExtractJsonError(result, Loc.S("存档导入失败。")));
            return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(result.Output);
        }
        public Dictionary<string, object> OneClickImport(string action, string path, uint uid, bool allowOverwrite = false)
        {
            string script = AppFile("one_click_import.py");
            if (!File.Exists(script)) return Capture(action == "plan" ? "inspect" : action, path, uid, false, allowOverwrite);
            string arguments = "-B -X utf8 " + Quote(script) + " " + action + " --file " + Quote(path) + " --profile " + Quote(ProfilePath);
            if (uid > 0) arguments += " --uid " + uid.ToString();
            if (allowOverwrite) arguments += " --allow-overwrite";
            var result = ExecuteProcess(Python, arguments, 60000, true, CancellationToken.None);
            if (result.Code != 0) throw new InvalidOperationException(ExtractJsonError(result, Loc.S("一键导入失败。")));
            return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(result.Output);
        }
        public void PrepareProfile()
        {
            var result = Execute(Python, "-X utf8 " + Quote(AppFile("profile_manager.py")) + " prepare --root " + Quote(Root) + " --base " + Quote(Path.Combine(BaseDir, "profiles")) + " --mode " + Mode + " --host " + Quote(Mode != "host" ? "127.0.0.1" : HostAddress));
            if (result.Code != 0) throw new InvalidOperationException(result.Error);
        }
        public void FocusClient()
        {
            foreach (var process in Process.GetProcessesByName("launcher"))
                using (process) try { if (String.Equals(process.MainModule.FileName, ClientExe, StringComparison.OrdinalIgnoreCase) && process.MainWindowHandle != IntPtr.Zero) Native.Focus(process.MainWindowHandle); } catch { }
        }
        public void EnableLanFirewall()
        {
            var arguments = "-NoProfile -ExecutionPolicy Bypass -File " + Quote(AppFile("Lan-Firewall.ps1")) + " -GameRoot " + Quote(Root) + " -HostAddress " + Quote(HostAddress) + " -ServerExe " + Quote(Path.Combine(BaseDir, "profiles", "host", "server", "Lolo.exe")) + " -PythonExe " + Quote(Python);
            using (var process = Process.Start(new ProcessStartInfo("powershell.exe", arguments) { UseShellExecute = true, Verb = "runas", WindowStyle = ProcessWindowStyle.Hidden }))
                if (!process.WaitForExit(30000) || process.ExitCode != 0) throw new InvalidOperationException(Loc.S("局域网权限未设置，请允许 Windows 管理员确认后重试。"));
        }
    }

    partial class LauncherForm : Form
    {
        readonly Backend backend = new Backend();
        readonly StatusTile server = new StatusTile(Loc.S("服务端")), gateway = new StatusTile(Loc.S("游戏网关")), proxy = new StatusTile(Loc.S("游戏连接"));
        readonly ActionButton start = new ActionButton(Loc.S("启动游戏  →"), true), services = new ActionButton(Loc.S("启动服务")), stop = new ActionButton(Loc.S("停止服务")), browse = new ActionButton(Loc.S("更改目录"));
        readonly ActionButton capturePreflight = new ActionButton(Loc.S("预检查")), captureStart = new ActionButton(Loc.S("开始采集")), captureStop = new ActionButton(Loc.S("结束采集")), captureSaveZip = new ActionButton(Loc.S("保存账号 ZIP"));
        string lastCaptureZip = "";
        bool lastCaptureIncomplete;
        readonly Label captureHint = new Label();
        readonly TextBox officialLauncherPath = new TextBox();
        readonly ActionButton officialBrowse = new ActionButton(Loc.S("选择启动器"));
        bool captureRunning, checkingCaptureProgress, checkingCaptureState, captureStartInFlight;
        readonly ListView captureChecklist = new ListView();
        readonly Label captureProgressHint = new Label();
        readonly System.Windows.Forms.Timer captureProgressTimer = new System.Windows.Forms.Timer();
        readonly ActionButton soloMode = new ActionButton(Loc.S("单机模式")), steamMode = new ActionButton(Loc.S("国际服")), hostMode = new ActionButton(Loc.S("局域网服务")), captureMode = new ActionButton(Loc.S("公服采集")), importData = new ActionButton(Loc.S("导入数据")), backupData = new ActionButton(Loc.S("备份存档")), openData = new ActionButton(Loc.S("数据目录")), firewall = new ActionButton(Loc.S("局域网权限")), roomsButton = new ActionButton(Loc.S("房间与玩家"));
        readonly ActionButton detectWorldClient = new ActionButton(Loc.S("查找 Steam")), captureDetectBtn = new ActionButton(Loc.S("查找 Steam"));
        readonly NumericUpDown uidBox = new NumericUpDown();
        readonly ComboBox hostBox = new ComboBox(), languageBox = new ComboBox(), worldChannelBox = new ComboBox(), captureChannelBox = new ComboBox();
        readonly Label profileLabel = new Label(), modeHint = new Label();
        Surface capturePage;
        Panel mainScroll;
        readonly Label captureSteps = new Label();
        readonly Label headline = new Label(), subtitle = new Label(), badge = new Label(), footer = new Label();
        Label sideTitleLabel, sideLangLabel, sideSectionLabel, soloDescLabel, steamDescLabel, hostDescLabel, captureDescLabel, roomsDescLabel, sideFooterDescLabel;
        Label heroBrandLabel, settingsTitleLabel, worldChannelLabel, channelStatusLabel, addressLabel;
        Label captureTitleLabel, captureChannelLabel, capturePathDescLabel, captureListTitleLabel, logsTitleLabel;
        LinkLabel openLogsLink;
        bool updatingUiControls, userSelectedLanguage, skipIpLocale;
        Dictionary<string, object> lastCaptureProgressData;
        string lastCaptureProgressSourceKey = "";
        readonly TextBox rootPath = new TextBox();
        readonly RichTextBox logs = new RichTextBox();
        readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
        bool busy, checking, closing, operationCancellable;
        CancellationTokenSource operationCancellation;
        ServiceStatus lastStatus;
        string lastStatusError, smokePath;
        bool verify, uiPreview;
        string uiPreviewDir, uiPage = "";
        public LauncherForm(string[] args)
        {
            string requestedLanguage = null;
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--smoke-test" && i + 1 < args.Length) smokePath = args[++i];
                else if (args[i] == "--verify") verify = true;
                else if (args[i] == "--ui-preview" && i + 1 < args.Length) { uiPreview = true; uiPreviewDir = args[++i]; }
                else if (args[i] == "--ui-mode" && i + 1 < args.Length) { string m = args[++i]; if (Array.IndexOf(Backend.KnownModes, m) >= 0) backend.Mode = m; }
                else if (args[i] == "--ui-page" && i + 1 < args.Length) { uiPage = args[++i]; }
                else if (args[i] == "--language" && i + 1 < args.Length) { requestedLanguage = args[++i]; }
                else if (args[i] == "--no-ip-locale") { skipIpLocale = true; }
                else if (args[i] == "--ui-size" && i + 1 < args.Length) { string[] s = args[++i].Split('x'); int w, h; if (s.Length == 2 && Int32.TryParse(s[0], out w) && Int32.TryParse(s[1], out h)) ClientSize = new Size(Math.Max(1024, w), Math.Max(700, h)); }
            }
            if (!String.IsNullOrWhiteSpace(requestedLanguage)) { Loc.SetLocale(requestedLanguage); backend.Language = Loc.CurrentLocale; skipIpLocale = true; }
            Text = Loc.S("开放空间 · 本地启动器"); Font = Theme.Font(10); BackColor = Theme.Bg; ForeColor = Theme.Text;
            if (ClientSize.Width < 1040 || ClientSize.Height < 740) ClientSize = new Size(1240, 900);
            MinimumSize = new Size(1040, 740); StartPosition = FormStartPosition.CenterScreen;
            AutoScaleMode = AutoScaleMode.Dpi; DoubleBuffered = true;
            AllowDrop = true;
            DragEnter += delegate(object sender, DragEventArgs e)
            {
                if (!busy && !closing && e.Data != null && e.Data.GetDataPresent(DataFormats.FileDrop))
                    e.Effect = DragDropEffects.Copy;
            };
            DragDrop += async delegate(object sender, DragEventArgs e)
            {
                if (busy || closing || e.Data == null) return;
                var dropped = e.Data.GetData(DataFormats.FileDrop) as string[];
                if (dropped != null && dropped.Length > 0 && !String.IsNullOrWhiteSpace(dropped[0]))
                    await ImportCapturePath(dropped[0]);
            };
            string iconPath = backend.AppFile("launcher.ico");
            if (File.Exists(iconPath)) Icon = new Icon(iconPath);
            if (uiPreview) { backend.HostAddress = "192.168.1.10"; backend.Uid = 1000000; backend.Root = ""; }
            BuildLayout();
            RefreshLocalization();
            ConfigureAccessibility();
            ApplyMode();
            ShowCapturePage(false);
            languageBox.SelectedIndexChanged += delegate
            {
                if (updatingUiControls || closing || languageBox.SelectedIndex < 0 || languageBox.SelectedIndex >= Loc.SupportedLocales.Length) return;
                string selectedLocale = Loc.SupportedLocales[languageBox.SelectedIndex];
                userSelectedLanguage = true;
                backend.HasSavedLanguage = true;
                backend.LanguageSource = "user";
                Loc.SetLocale(selectedLocale);
                backend.Language = selectedLocale;
                if (!uiPreview) try { backend.Save(); } catch { }
                RefreshLocalization();
            };
            worldChannelBox.SelectedIndexChanged += delegate
            {
                if (updatingUiControls || closing || worldChannelBox.SelectedIndex < 0 || worldChannelBox.SelectedIndex >= Backend.KnownChannels.Length) return;
                string selectedChannel = Backend.KnownChannels[worldChannelBox.SelectedIndex];
                backend.Channel = selectedChannel;
                string stored = selectedChannel == "auto" ? backend.Root : backend.GetChannelRoot(selectedChannel);
                if (!String.IsNullOrWhiteSpace(stored)) backend.Root = stored;
                rootPath.Text = String.IsNullOrWhiteSpace(backend.Root) ? Loc.S("单机模式：请选择本地客户端目录") : backend.Root;
                UpdateChannelBadge();
                if (!uiPreview) try { backend.Save(); } catch { }
            };
            captureChannelBox.SelectedIndexChanged += delegate
            {
                if (updatingUiControls || closing || captureChannelBox.SelectedIndex < 0 || captureChannelBox.SelectedIndex >= Backend.KnownChannels.Length) return;
                string selectedChannel = Backend.KnownChannels[captureChannelBox.SelectedIndex];
                backend.CaptureChannel = selectedChannel;
                string stored = selectedChannel == "auto" ? backend.OfficialLauncherPath : backend.GetOfficialPath(selectedChannel);
                if (!String.IsNullOrWhiteSpace(stored)) backend.OfficialLauncherPath = stored;
                officialLauncherPath.Text = String.IsNullOrWhiteSpace(backend.OfficialLauncherPath) ? Loc.S("请选择公服启动器或未修改的原版客户端 EXE") : backend.OfficialLauncherPath;
                if (!uiPreview) try { backend.Save(); } catch { }
            };
            detectWorldClient.Click += async delegate { await DetectSteamClientAsync(false); };
            captureDetectBtn.Click += async delegate { await DetectSteamClientAsync(true); };
            soloMode.Click += async delegate { await SwitchMode("solo"); };
            steamMode.Click += async delegate { await SwitchMode("steam"); };
            captureMode.Click += async delegate { ShowCapturePage(true); if (!uiPreview) { await RefreshCaptureState(); await RefreshCaptureProgress(); } };
            officialBrowse.Click += async delegate { await ChooseOfficialLauncher(); };
            capturePreflight.Click += async delegate {
                if(busy || captureRunning || uiPreview) return;
                SetBusy(true, Loc.S("正在预检查采集环境"));
                try {
                    var report=await Task.Run(() => backend.Official("preflight"));
                    string detail=PreflightText(report);
                    Append(detail);
                    MessageBox.Show(this,PreflightText(report,true),Loc.S("采集预检查"),MessageBoxButtons.OK,
                        report.ContainsKey("ok") && Convert.ToBoolean(report["ok"]) ? MessageBoxIcon.Information : MessageBoxIcon.Warning);
                } catch(Exception error) { string friendly=Friendly.Describe(error,Loc.S("采集预检查")); Append(friendly+"  "+Loc.S("详细原因：")+Friendly.Detail(error)); MessageBox.Show(this,Friendly.WithCaptureCauses(friendly),Loc.S("采集预检查"),MessageBoxButtons.OK,MessageBoxIcon.Warning); }
                finally { SetBusy(false, ""); }
            };
            captureStop.Enabled = false;
            captureSaveZip.Enabled = false;
            captureSaveZip.Click += async delegate {
                if(busy || captureRunning || !File.Exists(lastCaptureZip)) return;
                SetBusy(true, Loc.S("正在另存账号 ZIP"));
                try { await SaveCaptureZip(lastCaptureZip); }
                catch(Exception error) { ReportError("另存账号 ZIP", error, true); }
                finally { SetBusy(false, ""); }
            };
            hostMode.Click += async delegate { await SwitchMode("host"); };
            uidBox.ValueChanged += delegate { if (!busy) { backend.Uid=(uint)uidBox.Value; backend.Save(); } };
            hostBox.SelectedIndexChanged += delegate { if (!busy && backend.Mode=="host" && hostBox.SelectedItem != null) { backend.HostAddress=hostBox.SelectedItem.ToString(); backend.Save(); } };
            captureStart.Click += async delegate
            {
                // Claim the whole chooser/preflight/start chain, not just the
                // subprocess call, so rapid clicks cannot launch two collectors.
                if (busy || captureRunning || captureStartInFlight || uiPreview) return;
                captureStartInFlight = true;
                SetBusy(true, Loc.S("正在准备采集"));
                try
                {
                    if(String.IsNullOrWhiteSpace(backend.OfficialLauncherPath))
                    {
                        if(!await ChooseOfficialLauncher(true)) return;
                    }
                    if(closing)return;
                    SetBusy(true, Loc.S("正在预检查采集环境"));
                    var report = await Task.Run(() => backend.Official("preflight"));
                    if(closing)return;
                    if (OnlyPrivateWorldBlockedPreflight(report))
                    {
                        var activeModes = backend.ActiveWorldModes();
                        foreach (string activeMode in activeModes)
                        {
                            var st = await Task.Run(() => backend.StatusForMode(activeMode));
                            if (closing) return;
                            if (st != null && st.ClientRunning)
                            {
                                string msg = Loc.S("请先退出私服游戏，再开始公服采集，避免流量混入私服。");
                                Append(msg);
                                captureHint.Text = msg;
                                MessageBox.Show(this, msg, Loc.S("请先退出游戏"), MessageBoxButtons.OK, MessageBoxIcon.Warning);
                                return;
                            }
                        }
                        if (activeModes.Count > 0)
                        {
                            SetBusy(true, Loc.S("正在停止私服服务并准备采集"));
                            foreach (string activeMode in activeModes)
                                await Task.Run(() => backend.RunMode(activeMode, "Stop"));
                            if (closing) return;
                            Append(Loc.S("已自动停止运行中的私服服务。"));
                            report = await Task.Run(() => backend.Official("preflight"));
                            if (closing) return;
                        }
                    }
                    Append(PreflightText(report));
                    if(report.ContainsKey("needsElevation") && Convert.ToBoolean(report["needsElevation"]))
                    {
                        backend.Save();
                        Append(Loc.S("正在申请管理员权限；取消不会启动游戏或采集。"));
                        using(var elevated=Process.Start(new ProcessStartInfo(Application.ExecutablePath,
                            "--capture-after-elevation " + Process.GetCurrentProcess().Id.ToString()) { UseShellExecute=true,Verb="runas" }))
                        { if(elevated==null) throw new InvalidOperationException(Loc.S("未启动管理员实例，请重试。")); }
                        Close(); return;
                    }
                    if(!report.ContainsKey("ok") || !Convert.ToBoolean(report["ok"]))
                    { captureHint.Text=Loc.S("预检查未通过，请按检查结果处理后重试。");MessageBox.Show(this,PreflightText(report,true),Loc.S("采集预检查"));return; }
                    SetBusy(true, Loc.S("正在确认抓包就绪并启动公服"));
                    string exitFlag=Path.Combine(backend.BaseDir,"runtime","capture-launcher-exit.flag");
                    if(File.Exists(exitFlag))File.Delete(exitFlag);
                    backend.CaptureStartInFlight=true;
                    var result = await Task.Run(() => backend.Official("start"));
                    backend.CaptureStartInFlight=false;
                    if(closing)return;
                    if(!result.ContainsKey("officialKind")) throw new InvalidOperationException(Loc.S("采集启动结果不完整，请检查状态后重试。"));
                    captureRunning = true; lastCaptureZip = ""; lastCaptureIncomplete = false;
                    lastCaptureProgressData = null;
                    lastCaptureProgressSourceKey = "";
                    captureSaveZip.Enabled = false;
                    PopulateInitialChecklistRows();
                    captureProgressHint.Text = Loc.S("只有成功解码的公服响应才算已采集；快照暂不可读会显示暂时无法判断，结束后按完整数据核对。");
                    bool steam = Convert.ToString(result["officialKind"])=="steam";
                    Append(steam ? Loc.S("抓包已就绪，正在通过 Steam 启动客户端。公服路径未使用私服副本或本地UID。") : Loc.S("抓包已就绪，已启动配置的公服启动器。公服路径未使用私服副本或本地UID。"));
                    if (steam && result.ContainsKey("officialPid") && Convert.ToInt32(result["officialPid"])==0)
                        Append(Loc.S("Steam 正在启动游戏；如果它弹出登录或更新窗口，请先完成，进入游戏后开始加载要保存的界面。"));
                    if (result.ContainsKey("hint")) Append(Loc.S(Convert.ToString(result["hint"])));
                    captureHint.Text = Loc.S("采集中：公服启动器已打开，请正常登录并加载要保存的界面，完成后点「结束采集」。");
                }
                catch (Exception error)
                {
                    ReportError("开始采集", error, true);
                    if (!uiPreview && smokePath == null && !closing)
                        MessageBox.Show(this, Friendly.WithCaptureCauses(Friendly.Describe(error, Loc.S("开始采集"))), Loc.S("开始采集失败"), MessageBoxButtons.OK, MessageBoxIcon.Warning);
                }
                finally { captureStartInFlight = false; backend.CaptureStartInFlight=false; SetBusy(false, ""); }
                await RefreshCaptureState();
                await RefreshCaptureProgress();
            };
            captureStop.Click += async delegate
            {
                if (busy || !captureRunning) return;
                SetBusy(true, Loc.S("正在结束采集并生成账号 ZIP"));
                string step = "结束采集";
                try
                {
                    var captured = await Task.Run(() => backend.Official("stop"));
                    captureRunning = false;
                    lastCaptureProgressSourceKey = "";
                    await RefreshCaptureProgress();
                    string captureWarning = CaptureIncompleteWarning(captured);
                    lastCaptureIncomplete = captureWarning.Length > 0;
                    if (lastCaptureIncomplete)
                    {
                        Append(captureWarning);
                        captureProgressHint.Text = captureWarning;
                    }
                    string zip = captured.ContainsKey("accountZip") ? Convert.ToString(captured["accountZip"]) : "";
                    if (String.IsNullOrWhiteSpace(zip) || !System.IO.File.Exists(zip))
                    {
                        Append(Loc.S("采集结束，但没有生成账号包。请确认登录公服后确实进入了游戏场景。"));
                        captureHint.Text = Loc.S("未获得账号数据，可重新采集。");
                        return;
                    }
                    lastCaptureZip = zip;
                    bool captureIncomplete=false;
                    if(captured.ContainsKey("quality")) {
                        var quality=captured["quality"] as Dictionary<string,object>;
                        if(quality!=null && quality.ContainsKey("successful_decode_count")) Append(Loc.S("成功解码响应/消息数：")+Convert.ToString(quality["successful_decode_count"]));
                        if(quality!=null && quality.ContainsKey("trailing_bytes_count") && Convert.ToInt64(quality["trailing_bytes_count"])>0) Append(Loc.S("发现残缺流尾；请查看采集报告，不能视为完整备份。"));
                        if(quality!=null && quality.ContainsKey("tcp_gap_count") && Convert.ToInt64(quality["tcp_gap_count"])>0) { captureIncomplete=true; Append(Loc.S("检测到抓包缺口或未解码数据，备份可能不完整。")); }
                    }
                    if(captureIncomplete) captureHint.Text=Loc.S("检测到抓包缺口或未解码数据，备份可能不完整。");
                    if(captured.ContainsKey("myScenes")) {
                        var scenes=captured["myScenes"] as Dictionary<string,object>;
                        int sceneCount=scenes!=null && scenes.ContainsKey("sceneCount") ? Convert.ToInt32(scenes["sceneCount"]) : 0;
                        if(sceneCount>0) Append(Loc.S("已保存自己频道的场景备份：")+sceneCount+Loc.S(" 个，见 my-scenes 目录；他人频道不纳入。"));
                    }
                    if(captured.ContainsKey("exploration")) {
                        var exploration=captured["exploration"] as Dictionary<string,object>;
                        if(exploration!=null) Append(exploration.ContainsKey("evidence_available") && Convert.ToBoolean(exploration["evidence_available"])
                            ? Loc.S("探索观测报告已加入账号ZIP；总量未验证，不表示全图完整。")
                            : Loc.S("探索报告已生成，但尚未收到有效探索证据；未知不当0。"));
                    }
                    step = "保存账号 ZIP";
                    if(!await SaveCaptureZip(zip)) return;
                    step = "账号入库";
                    uint loginUid = (uint)uidBox.Value;
                    var review = await Task.Run(() => backend.OfficialImport("plan", zip, loginUid, false));
                    bool conflict = review.ContainsKey("conflict") && Convert.ToBoolean(review["conflict"]);
                    ServiceStatus serviceState = null;
                    if (conflict)
                    {
                        serviceState = await Task.Run(() => backend.Status());
                        if (serviceState.ClientRunning)
                        {
                            string runningMessage = Loc.S("检测到本机已有 UID ") + Convert.ToString(review["uid"]) + Loc.S(" 的存档，但当前私服游戏仍在运行；请先退出游戏后再重复导入覆盖。");
                            Append(runningMessage);
                            captureHint.Text = Loc.S("账号 ZIP 已交付；游戏运行中，未覆盖原有存档。");
                            MessageBox.Show(this, runningMessage, Loc.S("请先退出游戏"), MessageBoxButtons.OK, MessageBoxIcon.Warning);
                            return;
                        }
                    }
                    string message = conflict
                        ? Loc.S("检测到本机已有 UID ") + Convert.ToString(review["uid"]) + " · " + Convert.ToString(review["name"])
                            + Loc.S(" 的存档。\n\n继续覆盖导入会先自动备份当前数据库与旧导入记录，再用本次采集数据更新该 UID（其他 UID 保留")
                            + (serviceState != null && !String.IsNullOrEmpty(serviceState.RuntimeMode) ? Loc.S("，运行中的服务会先停止") : "")
                            + Loc.S("）。\n等级 ") + Convert.ToString(review["level"]) + " · " + Convert.ToString(review["characters"]) + Loc.S(" 个角色\n\n确认覆盖导入？")
                        : Loc.S("账号 ZIP 已保存交付。是否同时写入本机账号库？\nUID ") + Convert.ToString(review["uid"]) +
                            " · " + Convert.ToString(review["name"]) + Loc.S("\n等级 ") + Convert.ToString(review["level"]) +
                            " · " + Convert.ToString(review["characters"]) + Loc.S(" 个角色\n\n确认写入？");
                    bool incomplete = review.ContainsKey("quality") && Convert.ToBoolean(((Dictionary<string, object>)review["quality"])["incomplete"]);
                    if (incomplete)
                    {
                        var quality = (Dictionary<string, object>)review["quality"];
                        string detail = Loc.S("占位字段 ") + (Convert.ToInt32(quality["placeholderFields"]) + Convert.ToInt32(quality["characterPlaceholderFields"]))
                            + Loc.S(" 个，装备响应 ") + Convert.ToString(quality["equipmentResponses"]) + Loc.S(" 条");
                        string incompleteBody = Loc.S("这个包的解码不完整（") + detail + Loc.S("）。\n导入后外观与装备可能为空，但账号、等级、角色会写入。\n\n仍要导入吗？");
                        message = conflict ? incompleteBody + "\n\n" + message : incompleteBody;
                        Append(Loc.S("注意：包体解码不完整，装备与外观可能为空。"));
                    }
                    if (captureWarning.Length > 0) message = captureWarning + "\n\n" + message;
                    string promptTitle = incomplete ? Loc.S("数据不完整，确认导入") : conflict ? Loc.S("确认覆盖已有 UID 存档") : Loc.S("确认入库");
                    if (MessageBox.Show(this, message, promptTitle, MessageBoxButtons.OKCancel, MessageBoxIcon.Warning) != DialogResult.OK)
                    {
                        if (conflict)
                        {
                            Append(Loc.S("账号 ZIP 已交付；已取消覆盖原有 UID 存档。"));
                            captureHint.Text = Loc.S("账号 ZIP 已交付；已取消覆盖，原有存档保持不变。");
                        }
                        return;
                    }
                    if (conflict && serviceState != null && !String.IsNullOrEmpty(serviceState.RuntimeMode))
                    {
                        await Task.Run(() => backend.Run("Stop"));
                    }
                    if(!File.Exists(Path.Combine(backend.ProfilePath,"db","lolo.db")))
                        await Task.Run(() => backend.PrepareProfile());
                    var imported = await Task.Run(() => backend.OfficialImport("import", zip, loginUid, false, incomplete, conflict));
                    uint usedUid = Convert.ToUInt32(imported["uid"]);
                    backend.Uid = usedUid; backend.Save(); uidBox.Value = usedUid;
                    Append((conflict ? Loc.S("已覆盖更新数据库中的 UID ") : Loc.S("账号已写入数据库：UID ")) + usedUid + "（" + Convert.ToString(imported["name"]) + "）。");
                    AppendImportSummary(imported);
                    captureHint.Text = Loc.S(conflict ? "已覆盖更新：UID " : "已入库：UID ") + usedUid + Loc.S("。可用该 UID 登录当前模式。");
                }
                catch (Exception error)
                {
                    ReportError(step, error, true);
                    if (!uiPreview && smokePath == null && !closing)
                        MessageBox.Show(this, Friendly.WithCaptureCauses(Friendly.Describe(error, Loc.S(step))), Loc.S(step), MessageBoxButtons.OK, MessageBoxIcon.Warning);
                }
                finally { SetBusy(false, ""); }
                await RefreshCaptureState();
            };
            importData.Click += async delegate { await ImportCapture(); };
            backupData.Click += async delegate { await Operate("Backup", false); };
            openData.Click += delegate { OpenFolder(backend.ProfilePath); };
            firewall.Click += async delegate
            {
                if (busy || backend.Mode != "host") return;
                string blocked=PrivateOperationBlockReason(); if(blocked.Length>0) { Append(blocked); return; }
                SetBusy(true,Loc.S("正在设置局域网权限"));
                try { await Task.Run(() => { backend.PrepareProfile(); backend.EnableLanFirewall(); }); Append(Loc.S("已允许同一局域网访问当前服务。")); }
                catch (Exception e) { ReportError("设置局域网权限", e, false); }
                finally { SetBusy(false,""); }
                await RefreshStatus();
            };
            start.Click += async delegate { await Operate("Start", backend.Mode!="host"); };
            services.Click += async delegate { await Operate("Start", false); };
            stop.Click += async delegate
            {
                if (busy && operationCancellable && operationCancellation != null) { stop.Enabled = false; stop.Text = Loc.S("正在取消…"); operationCancellation.Cancel(); }
                else await Operate("Stop", false);
            };
            browse.Click += BrowseRoot;
            roomsButton.Click += delegate { string blocked=PrivateOperationBlockReason(); if(blocked.Length>0)Append(blocked);else if(!busy) { using(var dialog=new RoomManagerForm(backend)) dialog.ShowDialog(this); } };
            timer.Interval = 5000; timer.Tick += async delegate { await RefreshStatus(); };
            captureProgressTimer.Interval=1000;
            captureProgressTimer.Tick+=async delegate { if(capturePage.Visible && !uiPreview && !captureStartInFlight && !busy) await RefreshCaptureProgress(); };
            Shown += async delegate
            {
                if(uiPreview) {
                    try
                    {
                        await Task.Delay(400);
                        if(uiPage=="capture" || uiPage=="capture-return") ShowCapturePage(true);
                        if(uiPage=="capture-return") {
                            await SwitchMode(backend.Mode);
                            if(capturePage.Visible) { SaveUIPreview(uiPreviewDir); Visible=false; Environment.Exit(2); return; }
                        }
                        await Task.Delay(150); SaveUIPreview(uiPreviewDir); Visible=false; Environment.Exit(0);
                    }
                    catch (Exception error)
                    {
                        bool complete = Native.PreviewComplete(uiPreviewDir);
                        if (!complete) Native.WritePreviewError(uiPreviewDir, error);
                        Environment.Exit(complete ? 0 : 3);
                    }
                    return;
                }
                Append(Loc.S("启动器已就绪，正在读取本地服务状态。"));
                bool captureElevatedStart = Array.IndexOf(args, "--capture-after-elevation") >= 0;
                if (!skipIpLocale && !backend.HasSavedLanguage && !captureElevatedStart)
                {
                    var unusedIpLocale = InitializeLanguageFromIpAsync();
                }
                await RefreshStatus(); if (closing) return; timer.Start(); captureProgressTimer.Start();
                if (verify) await Verify();
                if (Array.IndexOf(args,"--rooms-smoke")>=0)
                {
                    using(var dialog=new RoomManagerForm(backend))
                    {
                        dialog.Shown+=async delegate { await Task.Delay(1200);using(var bitmap=new Bitmap(dialog.Width,dialog.Height)) { dialog.DrawToBitmap(bitmap,new Rectangle(0,0,dialog.Width,dialog.Height));bitmap.Save(Path.Combine(backend.BaseDir,"runtime","rooms-preview.png")); } dialog.Close(); };
                        dialog.ShowDialog(this);
                    }
                }
                if (Array.IndexOf(args,"--capture-after-elevation")>=0 && !closing)
                { ShowCapturePage(true);captureStart.PerformClick(); }
                if (smokePath != null) { await Task.Delay(600); SaveSmoke(); }
            };
            FormClosing += async delegate(object sender, FormClosingEventArgs e)
            {
                if (closing) return;
                closing = true;
                timer.Stop(); timer.Dispose();
                captureProgressTimer.Stop(); captureProgressTimer.Dispose();
                backend.CancelAll();
                // Detached services would otherwise survive this process and keep
                // holding 18080/18100. Reap only what this launcher started.
                //
                // Only the asynchronous cleanup path may cancel the close. Every
                // other reason must let the form close: the update handoff relies on
                // this process exiting so the waiting helper can take over, a Windows
                // shutdown must not be blocked, and the smoke run has to return.
                bool needsCleanup = !updaterHandoff && !uiPreview && smokePath == null
                    && e.CloseReason != CloseReason.WindowsShutDown;
                if (!needsCleanup) return;
                e.Cancel = true;
                Enabled = false;
                backend.StopCaptureQuietly();
                // Cleanup runs off the UI thread and has a hard close deadline.
                // A stuck controller must not freeze the window or block shutdown.
                var shutdownDeadline = new System.Windows.Forms.Timer { Interval = 3000 };
                shutdownDeadline.Tick += delegate { shutdownDeadline.Dispose(); Environment.Exit(0); };
                shutdownDeadline.Start();
                try { await Task.Run(() => backend.Cleanup()); }
                catch { }
                finally { shutdownDeadline.Dispose(); Environment.Exit(0); }
            };
            InitializeUpdater(args);
        }
        void ConfigureAccessibility()
        {
            AccessibleName = Loc.S("开放空间 · 本地启动器");
            AccessibleDescription = Loc.S("选择运行模式，查看服务状态并管理本地游戏与存档。");
            logs.AccessibleName = Loc.S("运行记录"); logs.AccessibleDescription = Loc.S("只读操作与错误记录");
            rootPath.AccessibleName = Loc.S("游戏目录"); officialLauncherPath.AccessibleName = Loc.S("公服启动器路径");
            uidBox.AccessibleName = "UID"; hostBox.AccessibleName = Loc.S("服务地址");
            captureChecklist.AccessibleName = Loc.S("采集项目清单"); captureHint.AccessibleName = Loc.S("采集提示");
            captureProgressHint.AccessibleName = Loc.S("采集进度"); badge.AccessibleName = Loc.S("状态");
            profileLabel.AccessibleName = Loc.S("独立数据目录");
            foreach (var button in new[] { soloMode, steamMode, hostMode, captureMode })
                button.AccessibleRole = AccessibleRole.RadioButton;
            var order = new Control[] { soloMode, steamMode, hostMode, captureMode, rootPath, browse, uidBox, hostBox,
                start, services, stop, importData, backupData, openData, firewall, roomsButton,
                officialLauncherPath, officialBrowse, capturePreflight, captureStart, captureStop, captureSaveZip,
                captureChecklist, logs };
            for (int i = 0; i < order.Length; i++) order[i].TabIndex = i;
            UpdateDynamicAccessibility();
        }
        void UpdateDynamicAccessibility()
        {
            if (badge != null) badge.AccessibleDescription = badge.Text;
            if (captureHint != null) captureHint.AccessibleDescription = captureHint.Text;
            if (captureProgressHint != null) captureProgressHint.AccessibleDescription = captureProgressHint.Text;
            UpdateModeAccessibility();
        }
        void UpdateModeAccessibility()
        {
            if (soloMode == null) return;
            soloMode.AccessibleDescription = Loc.S("单机模式") + (backend.Mode == "solo" && !captureMode.Primary ? " · " + Loc.S("已选中") : "");
            steamMode.AccessibleDescription = Loc.S("国际服") + (backend.Mode == "steam" && !captureMode.Primary ? " · " + Loc.S("已选中") : "");
            hostMode.AccessibleDescription = Loc.S("局域网服务") + (backend.Mode == "host" && !captureMode.Primary ? " · " + Loc.S("已选中") : "");
            captureMode.AccessibleDescription = Loc.S("公服采集") + (captureMode.Primary ? " · " + Loc.S("已选中") : "");
        }
        internal static string PrivateWorldBlockReason(bool captureRunning)
        { return captureRunning ? Loc.S("公服采集进行中，请先结束采集，再操作私服服务或切换模式。") : ""; }
        string PrivateOperationBlockReason()
        { return PrivateWorldBlockReason(captureRunning || backend.CaptureActive()); }
        internal static bool StatusMatchesMode(string requestedMode, ServiceStatus status)
        { return status != null && String.Equals(requestedMode, status.Mode, StringComparison.OrdinalIgnoreCase); }
        internal static string StartButtonText(string mode, ServiceStatus status)
        {
            if (mode == "host") return Loc.S("启动局域网服务  →");
            if (status != null && status.ClientRunning) return Loc.S("返回游戏  →");
            return mode == "steam" ? Loc.S("启动游戏  →") : Loc.S("启动单机游戏  →");
        }
        internal static string OperationSuccessMessage(string action, bool launch, Result result, ServiceStatus status, uint uid)
        {
            if (result == null) throw new InvalidOperationException(Loc.S("服务控制程序未返回有效结果，请查看运行记录。"));
            if (result.Code != 0) throw new InvalidOperationException(!String.IsNullOrWhiteSpace(result.Error) ? result.Error : !String.IsNullOrWhiteSpace(result.Output) ? result.Output : Loc.S("操作失败：") + action);
            if (status == null) throw new InvalidOperationException(Loc.S("操作后无法确认服务状态，请查看状态后重试。"));
            if (action == "Stop")
            {
                if (status.ServerRunning || status.ProxyRunning || !String.IsNullOrEmpty(status.RuntimeMode))
                    throw new InvalidOperationException(Loc.S("停止请求已返回，但状态检查仍显示服务正在运行。"));
                return Loc.S("当前模式的服务已停止，原网络路线已恢复。");
            }
            if (action == "Backup")
            {
                if (String.IsNullOrWhiteSpace(result.Output)) throw new InvalidOperationException(Loc.S("备份请求已返回，但没有确认备份文件，请重试。"));
                return Loc.S("备份已保存：") + result.Output;
            }
            if (action != "Start") throw new InvalidOperationException(Loc.S("未知服务操作：") + action);
            if (!status.Ready) throw new InvalidOperationException(Loc.S("启动请求已返回，但服务尚未就绪。请查看各服务状态后重试。"));
            if (launch && !status.ClientRunning) throw new InvalidOperationException(Loc.S("服务已就绪，但未检测到游戏客户端。请查看客户端日志后重试。"));
            if (!launch) return Loc.S("本地服务与游戏线路已就绪。");
            if (result.Output.IndexOf("Client already running", StringComparison.OrdinalIgnoreCase) >= 0) return Loc.S("游戏已经运行，已保留当前进度。");
            return Loc.S("游戏已打开，使用本地 UID ") + uid + Loc.S(" 登录。");
        }
        internal static string CaptureIncompleteWarning(Dictionary<string,object> result)
        {
            if (result == null || !result.ContainsKey("incompleteStreams") || result["incompleteStreams"] == null) return "";
            long count;
            try { count = Convert.ToInt64(result["incompleteStreams"]); }
            catch (FormatException) { return ""; }
            catch (InvalidCastException) { return ""; }
            catch (OverflowException) { return ""; }
            return count > 0 ? Loc.S("检测到抓包缺口或未解析的流尾部，备份可能不完整；仍可保存已解码账号 ZIP。") : "";
        }
        static bool OnlyPrivateWorldBlockedPreflight(Dictionary<string, object> report)
        {
            if (report == null || !report.ContainsKey("checks")) return false;
            if (report.ContainsKey("ok") && Convert.ToBoolean(report["ok"])) return false;
            if (report.ContainsKey("needsElevation") && Convert.ToBoolean(report["needsElevation"])) return false;
            bool hasPrivateWorldError = false;
            var checks = report["checks"] as System.Collections.IEnumerable;
            if (checks == null) return false;
            foreach (object entry in checks)
            {
                var row = entry as Dictionary<string, object>;
                if (row == null) continue;
                string id = row.ContainsKey("id") ? Convert.ToString(row["id"]) : "";
                string state = row.ContainsKey("state") ? Convert.ToString(row["state"]) : "";
                if (id == "location" || id == "output_permissions" || id == "administrator" || id == "pktmon") continue;
                if (id == "private_world")
                {
                    if (state == "error" || state == "unknown") hasPrivateWorldError = true;
                    continue;
                }
                if (state != "ok") return false;
            }
            return hasPrivateWorldError;
        }
        string PreflightText(Dictionary<string,object> report, bool compact = false)
        {
            var text=new StringBuilder();
            bool ready=report.ContainsKey("ok") && Convert.ToBoolean(report["ok"]);
            bool elevate=report.ContainsKey("needsElevation") && Convert.ToBoolean(report["needsElevation"]);
            text.AppendLine(ready ? Loc.S("采集依赖检查通过。") : elevate ? Loc.S("需要管理员确认；提升权限后会重新检查。") : Loc.S("预检查未通过，请按检查结果处理后重试。"));
            if(report.ContainsKey("checks")) foreach(object value in (System.Collections.IEnumerable)report["checks"])
            {
                var row=value as Dictionary<string,object>;if(row==null)continue;
                string state=row.ContainsKey("state")?Convert.ToString(row["state"]):"unknown";
                string label=state=="ok"?Loc.S("通过"):state=="warning"?Loc.S("注意"):state=="error"?Loc.S("失败"):Loc.S("未确认");
                string key=Loc.CurrentLocale == "en" && row.ContainsKey("messageEn")?"messageEn":"message";
                string message=row.ContainsKey(key)?Loc.S(Convert.ToString(row[key])):"";
                if(compact || state!="error"){message=message.Replace("\r","").Split('\n')[0];if(message.Length>240)message=message.Substring(0,240)+"…";}
                text.AppendLine("["+label+"] "+message);
            }
            return text.ToString();
        }
        Label LabelText(string text, float size, Color color, FontStyle style = FontStyle.Regular)
        {
            return new Label { Text = text, Font = Theme.Font(size, style), ForeColor = color, AutoSize = true, BackColor = Color.Transparent };
        }
        void BuildLegacyLayout()
        {
            var shell = new TableLayoutPanel { Dock=DockStyle.Fill, ColumnCount=2, RowCount=1, Margin=Padding.Empty, BackColor=Theme.Bg };
            shell.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,190)); shell.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100)); Controls.Add(shell);
            var sidebar = new Panel { Dock=DockStyle.Fill, BackColor=Theme.Panel, Padding=new Padding(18,24,18,20), Margin=Padding.Empty };
            shell.Controls.Add(sidebar,0,0);
            var sideBrand=LabelText(Loc.S("本 地 世 界"),13,Theme.Accent,FontStyle.Bold); sideBrand.Location=new Point(18,26); sidebar.Controls.Add(sideBrand);
            var sideSmall=LabelText("OVERFIELD",9,Theme.Muted); sideSmall.Location=new Point(19,62); sidebar.Controls.Add(sideSmall);
            var sideCaption=LabelText(Loc.S("运行模式"),9,Theme.Muted); sideCaption.Location=new Point(19,137); sidebar.Controls.Add(sideCaption);
            soloMode.Dock=DockStyle.None; soloMode.Location=new Point(16,169); soloMode.Size=new Size(156,48); sidebar.Controls.Add(soloMode);
            var soloDescription=LabelText(Loc.S("仅本机连接\n独立数据与存档"),9,Theme.Muted); soloDescription.Location=new Point(23,232); sidebar.Controls.Add(soloDescription);
            hostMode.Dock=DockStyle.None; hostMode.Location=new Point(16,303); hostMode.Size=new Size(156,48); sidebar.Controls.Add(hostMode);
            var hostDescription=LabelText(Loc.S("发布局域网服务\n独立联机存档"),9,Theme.Muted); hostDescription.Location=new Point(23,367); sidebar.Controls.Add(hostDescription);
            roomsButton.Dock=DockStyle.None; roomsButton.Location=new Point(16,435); roomsButton.Size=new Size(156,46); sidebar.Controls.Add(roomsButton);
            var localLogin=LabelText(Loc.S("UID 本地登录\n\n数据保存在本电脑\n支持各自导入与备份"),9,Theme.Muted); localLogin.Location=new Point(22,550); sidebar.Controls.Add(localLogin);
            var layout = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(26, 22, 26, 18), ColumnCount = 1, RowCount = 8, BackColor = Theme.Bg };
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            foreach (int h in new[] { 110, 84, 68, 126, 94, 112, 104, 30 }) layout.RowStyles.Add(new RowStyle(SizeType.Absolute, h));
            layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 28)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 0));
            shell.Controls.Add(layout,1,0);
            var hero = new Panel { Dock = DockStyle.Fill, Margin = Padding.Empty, BackColor = Theme.Bg };
            var brand = LabelText("O V E R F I E L D   /   L O C A L", 9, Theme.Accent, FontStyle.Bold); brand.Location = new Point(2, 0); hero.Controls.Add(brand);
            headline.Text = Loc.S("开放空间"); headline.Font = Theme.Font(30, FontStyle.Bold); headline.ForeColor = Theme.Text; headline.AutoSize = true; headline.Location = new Point(-3, 20); hero.Controls.Add(headline);
            subtitle.Text = Loc.S("你的世界，从这里出发。"); subtitle.Font = Theme.Font(10); subtitle.ForeColor = Theme.Muted; subtitle.AutoSize = true; subtitle.Location = new Point(2, 81); hero.Controls.Add(subtitle);
            badge.Text = Loc.S("检查中"); badge.Font = Theme.Font(10, FontStyle.Bold); badge.ForeColor = Theme.Muted; badge.TextAlign = ContentAlignment.MiddleRight; badge.Size = new Size(200, 35); badge.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            hero.Controls.Add(badge); hero.Resize += delegate { badge.Location = new Point(hero.Width - badge.Width, 5); };
            hero.Paint += delegate(object sender, PaintEventArgs e)
            {
                var g = e.Graphics; g.SmoothingMode = SmoothingMode.AntiAlias; float x = hero.Width - 184, y = 78;
                using (var p = new Pen(Color.FromArgb(45, 78, 85), 1.4f)) { g.DrawEllipse(p, x - 75, y - 20, 210, 41); g.DrawEllipse(p, x - 18, y - 50, 60, 94); }
                using (var b = new SolidBrush(Theme.Accent)) g.FillEllipse(b, x + 17, y - 6, 9, 9);
                using (var b = new SolidBrush(Color.FromArgb(64, 91, 109))) foreach (var s in new[] { new PointF(x - 88, y - 17), new PointF(x + 89, y - 52), new PointF(x + 135, y + 7) }) g.FillEllipse(b, s.X, s.Y, 3, 3);
            };
            layout.Controls.Add(hero, 0, 0);
            var states = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 3, RowCount = 1, Margin = new Padding(0, 0, 0, 10) };
            for (int i = 0; i < 3; i++) states.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 33.33f));
            proxy.Margin = Padding.Empty; states.Controls.Add(server, 0, 0); states.Controls.Add(gateway, 1, 0); states.Controls.Add(proxy, 2, 0); layout.Controls.Add(states, 0, 1);
            var actions = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 3, RowCount = 1, Margin = new Padding(0, 2, 0, 14) };
            actions.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50)); actions.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 25)); actions.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 25));
            stop.Margin = Padding.Empty; actions.Controls.Add(start, 0, 0); actions.Controls.Add(services, 1, 0); actions.Controls.Add(stop, 2, 0); layout.Controls.Add(actions, 0, 2);
            var directory = new Surface { Dock = DockStyle.Fill, Margin = new Padding(0, 0, 0, 14), Padding = new Padding(17, 12, 15, 12) };
            var pathTitle = LabelText(Loc.S("启动设置"), 9, Theme.Muted); pathTitle.Dock = DockStyle.Top; directory.Controls.Add(pathTitle);
            var pathRow = new TableLayoutPanel { Dock = DockStyle.Top, Height = 32, ColumnCount = 2, RowCount = 1, Margin = Padding.Empty, BackColor = Theme.Panel };
            pathRow.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); pathRow.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 105));
            rootPath.Text = String.IsNullOrWhiteSpace(backend.Root)?Loc.S("单机启动时选择客户端目录"):backend.Root; rootPath.ReadOnly = true; rootPath.BorderStyle = BorderStyle.None; rootPath.BackColor = Theme.Panel; rootPath.ForeColor = Theme.Text; rootPath.Dock = DockStyle.Fill; rootPath.Margin = new Padding(0, 6, 12, 0);
            browse.Font = Theme.Font(9); browse.Margin = Padding.Empty; pathRow.Controls.Add(rootPath, 0, 0); pathRow.Controls.Add(browse, 1, 0); directory.Controls.Add(pathRow); pathTitle.BringToFront();
            var uidRow=new TableLayoutPanel { Dock=DockStyle.Bottom, Height=32, ColumnCount=4, RowCount=1, BackColor=Theme.Panel };
            foreach(float width in new[]{80f,180f,90f}) uidRow.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,width)); uidRow.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
            var uidLabel=LabelText("UID",9,Theme.Muted); uidLabel.Dock=DockStyle.Fill; uidLabel.TextAlign=ContentAlignment.MiddleLeft;
            uidBox.Minimum=1; uidBox.Maximum=UInt32.MaxValue; uidBox.Value=backend.Uid; uidBox.Dock=DockStyle.Fill; uidBox.Font=Theme.Font(10); uidBox.BackColor=Theme.Panel; uidBox.ForeColor=Theme.Text; uidBox.Margin=new Padding(0,2,20,0);
            var addressLabel=LabelText(Loc.S("服务地址"),9,Theme.Muted); addressLabel.Dock=DockStyle.Fill; addressLabel.TextAlign=ContentAlignment.MiddleLeft;
            hostBox.DropDownStyle=ComboBoxStyle.DropDownList; hostBox.Dock=DockStyle.Fill; hostBox.Font=Theme.Font(9); hostBox.BackColor=Theme.Panel; hostBox.ForeColor=Theme.Text; hostBox.Margin=new Padding(0,2,0,0);
            uidRow.Controls.Add(uidLabel,0,0); uidRow.Controls.Add(uidBox,1,0); uidRow.Controls.Add(addressLabel,2,0); uidRow.Controls.Add(hostBox,3,0); directory.Controls.Add(uidRow); layout.Controls.Add(directory, 0, 3);
            directory.Controls.Clear();
            var settingRows=new TableLayoutPanel { Dock=DockStyle.Fill,ColumnCount=1,RowCount=3,Margin=Padding.Empty,BackColor=Theme.Panel };
            settingRows.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
            settingRows.RowStyles.Add(new RowStyle(SizeType.Absolute,22));settingRows.RowStyles.Add(new RowStyle(SizeType.Absolute,32));settingRows.RowStyles.Add(new RowStyle(SizeType.Absolute,32));
            pathTitle.Dock=pathRow.Dock=uidRow.Dock=DockStyle.Fill;pathTitle.Margin=pathRow.Margin=uidRow.Margin=Padding.Empty;
            settingRows.Controls.Add(pathTitle,0,0);settingRows.Controls.Add(pathRow,0,1);settingRows.Controls.Add(uidRow,0,2);directory.Controls.Add(settingRows);
            var dataPanel=new Surface { Dock=DockStyle.Fill, Margin=new Padding(0,0,0,14), Padding=new Padding(16,10,16,10) };
            profileLabel.Dock=DockStyle.Top; profileLabel.Height=24; profileLabel.Font=Theme.Font(8.5f); profileLabel.ForeColor=Theme.Muted; profileLabel.AutoEllipsis=true; dataPanel.Controls.Add(profileLabel);
            var dataActions=new TableLayoutPanel { Dock=DockStyle.Bottom, Height=31, ColumnCount=4, RowCount=1, BackColor=Theme.Panel };
            for(int i=0;i<4;i++) dataActions.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,25));
            int buttonIndex=0; foreach(var button in new[]{importData,backupData,openData,firewall}) { button.Font=Theme.Font(9); dataActions.Controls.Add(button,buttonIndex++,0); } firewall.Margin=Padding.Empty;
            dataPanel.Controls.Add(dataActions); layout.Controls.Add(dataPanel,0,4);
            var logPanel = new Surface { Dock = DockStyle.Fill, Margin = Padding.Empty, Padding = new Padding(16, 13, 16, 14) };
            var logHead = new Panel { Dock = DockStyle.Top, Height = 33, BackColor = Theme.Panel };
            logHead.Controls.Add(LabelText(Loc.S("运行记录"), 10, Theme.Text, FontStyle.Bold));
            var openLogs = new LinkLabel { Text = Loc.S("打开日志文件夹"), LinkColor = Theme.Muted, ActiveLinkColor = Theme.Accent, VisitedLinkColor = Theme.Muted, AutoSize = true, Anchor = AnchorStyles.Right | AnchorStyles.Top, BackColor = Theme.Panel };
            logHead.Controls.Add(openLogs); logHead.Resize += delegate { openLogs.Location = new Point(logHead.Width - openLogs.Width, 1); };
            openLogs.LinkClicked += delegate { OpenFolder(Path.Combine(backend.ProfilePath, "logs")); };
            logs.Dock = DockStyle.Fill; logs.ReadOnly = true; logs.BackColor = Theme.Panel; logs.ForeColor = Theme.Muted; logs.BorderStyle = BorderStyle.None; logs.Font = Theme.Font(9); logs.DetectUrls = false; logs.ScrollBars = RichTextBoxScrollBars.Vertical;
            logPanel.Controls.Add(logs); logPanel.Controls.Add(logHead); layout.Controls.Add(logPanel, 0, 6);
            footer.Text = Loc.S("可随时退出 · 游戏与服务独立运行 · 两种模式的存档分开保存"); footer.ForeColor = Theme.Muted; footer.Font = Theme.Font(8.5f); footer.Dock = DockStyle.Fill; footer.TextAlign = ContentAlignment.BottomLeft; layout.Controls.Add(footer, 0, 7);
        }
        async Task InitializeLanguageFromIpAsync()
        {
            try
            {
                string country = await Task.Run(() => FetchIpCountryCode(backend.LifetimeToken));
                if (closing || IsDisposed || userSelectedLanguage || backend.HasSavedLanguage) return;
                if (String.IsNullOrEmpty(Loc.ParseGeoCountryCode(country))) return;
                string resolved = Loc.LocaleFromCountryCode(country);
                backend.HasSavedLanguage = true;
                backend.LanguageSource = "ip";
                backend.Language = resolved;
                if (!String.Equals(Loc.CurrentLocale, resolved, StringComparison.OrdinalIgnoreCase))
                {
                    Loc.SetLocale(resolved);
                    updatingUiControls = true;
                    try
                    {
                        if (languageBox != null) languageBox.SelectedIndex = Loc.LocaleIndex(resolved);
                    }
                    finally { updatingUiControls = false; }
                    RefreshLocalization();
                }
                try { backend.Save(); } catch { }
                Append(Loc.S("已根据网络区域初始化语言：") + Loc.LocaleDisplayNames[Loc.LocaleIndex(resolved)]);
            }
            catch { }
        }
        static string FetchIpCountryCode(CancellationToken cancellation)
        {
            try { ServicePointManager.SecurityProtocol |= (SecurityProtocolType)3072; } catch { }
            foreach (string endpoint in new[] { "https://ipinfo.io/country", "https://api.country.is/" })
            {
                if (cancellation.IsCancellationRequested) return "";
                try
                {
                    var request = (HttpWebRequest)WebRequest.Create(endpoint);
                    request.Method = "GET";
                    request.UserAgent = "OverFieldLauncher";
                    request.Timeout = 2000;
                    request.ReadWriteTimeout = 2000;
                    request.AllowAutoRedirect = false;
                    using (cancellation.Register(delegate { try { request.Abort(); } catch { } }))
                    using (var response = (HttpWebResponse)request.GetResponse())
                    {
                        if ((int)response.StatusCode != 200) continue;
                        using (var stream = response.GetResponseStream())
                        using (var reader = new StreamReader(stream, Encoding.UTF8))
                        {
                            char[] buffer = new char[512];
                            int count = reader.ReadBlock(buffer, 0, buffer.Length);
                            string code = Loc.ParseGeoCountryCode(new string(buffer, 0, count));
                            if (!String.IsNullOrEmpty(code)) return code;
                        }
                    }
                }
                catch { }
            }
            return "";
        }
        async Task RefreshCaptureProgress()
        {
            if(checkingCaptureProgress || closing || uiPreview) return;
            checkingCaptureProgress=true;
            try {
                Dictionary<string, object> fastData = null;
                string nextSourceKey = lastCaptureProgressSourceKey;
                bool waiting = false, unchanged = false;
                bool handled = backend.TryReadCaptureProgressDirect(lastCaptureProgressSourceKey, out fastData, out nextSourceKey, out waiting, out unchanged);
                if (handled)
                {
                    lastCaptureProgressSourceKey = nextSourceKey;
                    if (unchanged || waiting || fastData == null) return;
                    RenderCaptureProgress(fastData);
                    return;
                }
                string statePath = Path.Combine(backend.BaseDir, "runtime", "capture-state.json");
                if (!File.Exists(statePath) && lastCaptureProgressData != null && !captureRunning && !captureStartInFlight) return;
                var data=await Task.Run(() => backend.Official("progress"));
                if(closing) return;
                RenderCaptureProgress(data);
            } catch(Exception error) { if(!closing) captureProgressHint.Text=Loc.S("项目状态暂不可读：")+Friendly.Describe(error,"采集进度"); }
            finally { checkingCaptureProgress=false; UpdateDynamicAccessibility(); }
        }
        void PopulateInitialChecklistRows()
        {
            if (captureChecklist == null) return;
            captureChecklist.Items.Clear();
            foreach (string label in new[]{
                "登录基础信息", "UID、等级与任务主数据", "角色与外观", "背包物品", "武器", "护甲",
                "装备海报", "宠物列表", "家园与家具", "邮件列表", "卡池信息", "抽卡记录",
                "成就", "图鉴", "探索与收集", "好友列表", "档案与生活技能", "钓鱼与图鉴", "采集与图鉴"})
            {
                var row = new ListViewItem(Loc.S(label));
                row.Tag = new[] { label, "等待核对", "" };
                row.SubItems.Add(Loc.S("等待核对"));
                row.SubItems.Add("0");
                row.SubItems.Add("");
                captureChecklist.Items.Add(row);
            }
        }
        void PopulateChannelComboBoxes()
        {
            updatingUiControls = true;
            try
            {
                string[] labels = { Loc.S("自动识别（全区服通用）"), Loc.S("B服（Bilibili）"), Loc.S("TapTap 服"), Loc.S("Steam 服") };
                if (worldChannelBox != null)
                {
                    worldChannelBox.Items.Clear();
                    foreach (string label in labels) worldChannelBox.Items.Add(label);
                    int idx = Array.IndexOf(Backend.KnownChannels, Backend.NormalizeChannel(backend.Channel));
                    worldChannelBox.SelectedIndex = idx >= 0 ? idx : 0;
                }
                if (captureChannelBox != null)
                {
                    captureChannelBox.Items.Clear();
                    foreach (string label in labels) captureChannelBox.Items.Add(label);
                    int idx = Array.IndexOf(Backend.KnownChannels, Backend.NormalizeChannel(backend.CaptureChannel));
                    captureChannelBox.SelectedIndex = idx >= 0 ? idx : 0;
                }
            }
            finally { updatingUiControls = false; }
        }
        void UpdateChannelBadge()
        {
            if (channelStatusLabel == null) return;
            if (String.IsNullOrWhiteSpace(backend.Root))
            {
                channelStatusLabel.Text = Loc.S("尚未配置客户端目录");
                channelStatusLabel.ForeColor = Theme.Muted;
                return;
            }
            string clientExe = Backend.FindClient(backend.Root);
            string detected = Backend.DetectChannel(clientExe);
            if (detected == "bilibili") { channelStatusLabel.Text = Loc.S("已识别：Bilibili（PCGameSDK）"); channelStatusLabel.ForeColor = Theme.Accent; }
            else if (detected == "taptap") { channelStatusLabel.Text = Loc.S("已识别：TapTap（QuickSDK PC）"); channelStatusLabel.ForeColor = Theme.Accent; }
            else if (detected == "steam") { channelStatusLabel.Text = Loc.S("已识别：Steam（QuickSDK Overseas）"); channelStatusLabel.ForeColor = Theme.Accent; }
            else { channelStatusLabel.Text = Loc.S("未在当前目录识别到有效客户端"); channelStatusLabel.ForeColor = Theme.Muted; }
        }
        async Task DetectSteamClientAsync(bool forCapture)
        {
            if (busy || (forCapture && captureRunning) || closing || uiPreview) return;
            SetBusy(true, Loc.S("正在检查游戏目录"));
            try
            {
                var info = await Task.Run(() => backend.Official("discover"));
                if (closing) return;
                var clients = info != null && info.ContainsKey("clients") ? info["clients"] as Dictionary<string, object> : null;
                var steam = clients != null && clients.ContainsKey("steam") ? clients["steam"] as Dictionary<string, object> : null;
                string exePath = steam != null && steam.ContainsKey("path") ? Convert.ToString(steam["path"]) : "";
                string dirPath = steam != null && steam.ContainsKey("dir") ? Convert.ToString(steam["dir"]) : "";
                if (String.IsNullOrWhiteSpace(exePath) || !File.Exists(exePath))
                {
                    string missing = Loc.S("未在本机 Steam 库中检测到《开放空间》(App 3879660)，请点击右侧按钮手动选择客户端目录或快捷方式。");
                    Append(missing);
                    MessageBox.Show(this, missing, Loc.S("查找 Steam"), MessageBoxButtons.OK, MessageBoxIcon.Information);
                    return;
                }
                if (forCapture)
                {
                    backend.SteamOfficialPath = exePath;
                    backend.OfficialLauncherPath = exePath;
                    backend.CaptureChannel = "steam";
                    backend.Save();
                    if (officialLauncherPath != null) officialLauncherPath.Text = exePath;
                    PopulateChannelComboBoxes();
                    Append(Loc.S("已找到 Steam 客户端：") + exePath);
                }
                else
                {
                    string selectedDir = !String.IsNullOrWhiteSpace(dirPath) ? dirPath : Path.GetDirectoryName(exePath);
                    backend.ValidateRoot(selectedDir);
                    backend.SteamRoot = selectedDir;
                    backend.Root = selectedDir;
                    backend.Channel = "steam";
                    backend.Save();
                    rootPath.Text = selectedDir;
                    PopulateChannelComboBoxes();
                    UpdateChannelBadge();
                    Append(Loc.S("已找到 Steam 客户端：") + selectedDir);
                }
            }
            catch (Exception error) { ReportError("查找 Steam", error, forCapture); }
            finally { SetBusy(false, ""); }
            if (!forCapture && !closing) await RefreshStatus();
        }
        void RefreshLocalization()
        {
            Text = Loc.S("开放空间 · 本地启动器");
            if (sideTitleLabel != null) sideTitleLabel.Text = Loc.S("开放空间");
            if (sideLangLabel != null) sideLangLabel.Text = Loc.S("语言");
            if (sideSectionLabel != null) sideSectionLabel.Text = Loc.S("选择你的世界");
            if (soloMode != null) soloMode.Text = Loc.S("单机模式");
            if (steamMode != null) steamMode.Text = Loc.S("国际服");
            if (hostMode != null) hostMode.Text = Loc.S("局域网服务");
            if (captureMode != null) captureMode.Text = Loc.S("公服采集");
            if (roomsButton != null) roomsButton.Text = Loc.S("房间与玩家");
            if (soloDescLabel != null) soloDescLabel.Text = Loc.S("存档 A · 全区服可用");
            if (steamDescLabel != null) steamDescLabel.Text = Loc.S("存档 B · 全区服可用");
            if (hostDescLabel != null) hostDescLabel.Text = Loc.S("局域网联机 · 全区服可用");
            if (captureDescLabel != null) captureDescLabel.Text = Loc.S("官方采集（B服 / Tap / Steam）");
            if (roomsDescLabel != null) roomsDescLabel.Text = Loc.S("查看在线玩家与房间");
            if (sideFooterDescLabel != null) sideFooterDescLabel.Text = Loc.S("以自己的 UID 开始\n数据只保存在本电脑\n请勿公开私人存档");
            if (heroBrandLabel != null) heroBrandLabel.Text = Loc.S("O V E R F I E L D  /  本 地 世 界");
            if (headline != null) headline.Text = Loc.S("开放空间");
            if (server != null) { server.Caption = Loc.S("服务端"); server.Invalidate(); }
            if (gateway != null) { gateway.Caption = Loc.S("游戏网关"); gateway.Invalidate(); }
            if (proxy != null) { proxy.Caption = Loc.S("UID 与房间网关"); proxy.Invalidate(); }
            if (settingsTitleLabel != null) settingsTitleLabel.Text = Loc.S("启动设置");
            if (worldChannelLabel != null) worldChannelLabel.Text = Loc.S("游戏区服");
            if (detectWorldClient != null) detectWorldClient.Text = Loc.S("查找 Steam");
            if (browse != null) browse.Text = Loc.S("更改目录");
            if (addressLabel != null) addressLabel.Text = Loc.S("服务地址");
            if (rootPath != null) rootPath.Text = String.IsNullOrWhiteSpace(backend.Root) ? Loc.S("单机模式：请选择本地客户端目录") : backend.Root;
            if (importData != null) importData.Text = Loc.S("导入数据");
            if (backupData != null) backupData.Text = Loc.S("备份存档");
            if (openData != null) openData.Text = Loc.S("数据目录");
            if (firewall != null) firewall.Text = backend.Mode == "host" && lastStatus != null && lastStatus.FirewallConfigured ? Loc.S("权限已配置") : Loc.S("局域网权限");
            if (captureTitleLabel != null) captureTitleLabel.Text = Loc.S("公服账号采集");
            if (captureChannelLabel != null) captureChannelLabel.Text = Loc.S("采集区服");
            if (captureDetectBtn != null) captureDetectBtn.Text = Loc.S("查找 Steam");
            if (capturePathDescLabel != null) capturePathDescLabel.Text = Loc.S("公服启动器路径（仅公服，与私服目录独立）");
            if (officialLauncherPath != null) officialLauncherPath.Text = String.IsNullOrWhiteSpace(backend.OfficialLauncherPath) ? Loc.S("请选择公服启动器或未修改的原版客户端 EXE") : backend.OfficialLauncherPath;
            if (officialBrowse != null) officialBrowse.Text = Loc.S("选择启动器");
            if (capturePreflight != null) capturePreflight.Text = Loc.S("预检查");
            if (captureStart != null) captureStart.Text = Loc.S("开始采集");
            if (captureStop != null) captureStop.Text = Loc.S("结束采集");
            if (captureSaveZip != null) captureSaveZip.Text = Loc.S("保存账号 ZIP");
            if (captureListTitleLabel != null) captureListTitleLabel.Text = Loc.S("采集项目清单 · 1.0.12增强集成 · 实时/最终核对");
            if (captureSteps != null) captureSteps.Text = Loc.S("1. 管理员运行，选择公服原版 EXE；不要选择私服副本\n2. 开始采集并登录，按清单打开缺少的界面并等待加载\n3. 结束后核对完整清单并另存 ZIP；入库可取消，私人数据勿公开");
            if (captureChecklist != null && captureChecklist.Columns.Count == 4)
            {
                captureChecklist.Columns[0].Text = Loc.S("项目");
                captureChecklist.Columns[1].Text = Loc.S("状态");
                captureChecklist.Columns[2].Text = Loc.S("响应");
                captureChecklist.Columns[3].Text = Loc.S("未采集时的操作建议");
            }
            if (logsTitleLabel != null) logsTitleLabel.Text = Loc.S("运行记录");
            if (openLogsLink != null) openLogsLink.Text = Loc.S("打开日志文件夹");
            if (footer != null) footer.Text = Loc.S("游戏与服务独立运行  ·  单机 / 联机存档分开保存");
            PopulateChannelComboBoxes();
            UpdateChannelBadge();
            RefreshUpdaterLocale();
            ApplyMode();
            if (lastStatus != null) ApplyStatus(lastStatus);
            else if (badge != null) badge.Text = Loc.S("正在检查");
            if (start != null && !busy) start.Text = StartButtonText(backend.Mode, lastStatus);
            if (stop != null && !busy) stop.Text = Loc.S("停止服务");
            if (lastCaptureProgressData != null) RenderCaptureProgress(lastCaptureProgressData);
            else
            {
                foreach (ListViewItem row in captureChecklist.Items)
                {
                    var raw = row.Tag as string[];
                    if (raw != null && raw.Length == 3) { row.Text = Loc.S(raw[0]); row.SubItems[1].Text = Loc.S(raw[1]); row.SubItems[3].Text = Loc.S(raw[2]); }
                }
                if (captureHint != null) captureHint.Text = Loc.S("抓包就绪后会打开公服。结束采集将生成账号 ZIP，并让你选择保存位置；入库是可选操作，取消或冲突不影响已交付的 ZIP。");
                if (captureProgressHint != null) captureProgressHint.Text = Loc.S("只有成功解码的公服响应才算已采集；快照暂不可读会显示暂时无法判断，结束后按完整数据核对。");
            }
            ConfigureAccessibility();
            PerformLayout();
        }
        void RenderCaptureProgress(Dictionary<string,object> data)
        {
            lastCaptureProgressData = data;
            captureChecklist.BeginUpdate();
            try {
                captureChecklist.Items.Clear();
                if(data.ContainsKey("items")) foreach(object item in (System.Collections.IEnumerable)data["items"]) {
                    var row=item as Dictionary<string,object>;if(row==null) continue;
                    string status=Convert.ToString(row["state"]);
                    string rawLabel=Convert.ToString(row["label"]), rawStatus=Convert.ToString(row["status"]), rawHint=Convert.ToString(row["hint"]);
                    var view=new ListViewItem(Loc.S(rawLabel));
                    view.Tag=new[]{rawLabel, rawStatus, rawHint};
                    view.SubItems.Add(Loc.S(rawStatus));view.SubItems.Add(Convert.ToString(row["count"]));view.SubItems.Add(Loc.S(rawHint));
                    view.ForeColor=status=="collected"?Color.FromArgb(36,128,100):(status=="missing" || status=="partial")?Color.FromArgb(168,99,38):Theme.Muted;
                    captureChecklist.Items.Add(view);
                }
                string warningKey=Loc.CurrentLocale == "en" && data.ContainsKey("warningEn")?"warningEn":"warning";
                string warning=data.ContainsKey(warningKey)?Loc.S(Convert.ToString(data[warningKey])):"";
                string stamp=data.ContainsKey("updatedAt")?Convert.ToString(data["updatedAt"]):"";
                object live=data.ContainsKey("livePackets")?data["livePackets"]:null;
                string packets=live!=null && Convert.ToInt64(live)>0 && !warning.Contains("PktMon") ? Loc.S(" · PktMon 已记录 ")+String.Format("{0:N0}",Convert.ToInt64(live))+Loc.S(" 个数据包") : "";
                captureProgressHint.Text=(String.IsNullOrWhiteSpace(warning)?Loc.S("最近核对：")+stamp:warning)+packets;
            } finally { captureChecklist.EndUpdate(); UpdateDynamicAccessibility(); }
        }
        async Task<bool> SaveCaptureZip(string zip)
        {
            if (lastCaptureIncomplete) Append(Loc.S("检测到抓包缺口或未解析的流尾部，备份可能不完整；仍可保存已解码账号 ZIP。"));
            string destination;
            using(var dialog = new SaveFileDialog { Title=Loc.S("保存采集账号 ZIP（交付文件）"), Filter=Loc.S("账号备份 ZIP|*.zip"), DefaultExt="zip", AddExtension=true, OverwritePrompt=true, CheckPathExists=true, RestoreDirectory=true, FileName=Path.GetFileName(zip), InitialDirectory=Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory) })
            {
                if(dialog.ShowDialog(this)!=DialogResult.OK) {
                    Append(Loc.S("已取消另存；原账号ZIP保留，可稍后点击「保存账号 ZIP」。"));
                    captureHint.Text=Loc.S("账号 ZIP 已生成但尚未另存交付，可点击「保存账号 ZIP」重试。");
                    return false;
                }
                destination=dialog.FileName;
            }
            var result=await Task.Run(() => backend.Official("export-zip", " --file " + Backend.Quote(zip) + " --destination " + Backend.Quote(destination)));
            string saved=Convert.ToString(result["path"]);
            if(String.IsNullOrWhiteSpace(saved) || !File.Exists(saved)) throw new InvalidOperationException(Loc.S("账号 ZIP 导出结果无效，请重试。"));
            Append(Loc.S("账号 ZIP 已交付：")+saved);
            captureHint.Text=Loc.S("账号 ZIP 已保存：")+saved+Loc.S("。可私下交给可信服主，入库为可选操作。")
                +(lastCaptureIncomplete?Loc.S("检测到抓包缺口或未解析的流尾部，备份可能不完整；仍可保存已解码账号 ZIP。"):"");
            try {
                string explorer=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows),"explorer.exe");
                Process.Start(new ProcessStartInfo(explorer,"/select,"+Backend.Quote(saved)){UseShellExecute=true});
            } catch(Exception error) { if(!closing) Append(Loc.S("文件已保存，但无法打开所在位置：")+Friendly.Describe(error,"打开所在位置")); }
            return true;
        }
        async Task<bool> ChooseOfficialLauncher(bool alreadyBusy = false)
        {
            if((!alreadyBusy && busy) || captureRunning || closing) return false;
            string selected;
            using(var dialog = new OpenFileDialog { Title=Loc.S("选择公服启动器、原版客户端，或 Steam 游戏快捷方式"), Filter=Loc.S("公服启动器 / Steam 快捷方式|*.exe;*.url|可执行文件|*.exe|Steam 快捷方式|*.url"), CheckFileExists=true, Multiselect=false, FileName=backend.OfficialLauncherPath })
            { if(dialog.ShowDialog(this)!=DialogResult.OK) return false; selected=dialog.FileName; }
            SetBusy(true, Loc.S("正在校验公服启动器路径"));
            try
            {
                var info=await Task.Run(() => backend.Official("validate-launcher", " --launcher " + Backend.Quote(selected)));
                if(closing) return false;
                string validatedPath = Convert.ToString(info["path"]);
                string detectedChannel = info.ContainsKey("channel") ? Convert.ToString(info["channel"]) : "unknown";
                backend.OfficialLauncherPath = validatedPath;
                if (detectedChannel != "unknown")
                {
                    backend.SetOfficialPath(detectedChannel, validatedPath);
                    backend.CaptureChannel = detectedChannel;
                }
                backend.Save(); officialLauncherPath.Text = backend.OfficialLauncherPath;
                PopulateChannelComboBoxes();
                if(info.ContainsKey("kind") && Convert.ToString(info["kind"])=="steam") {
                    string install=info.ContainsKey("install")?Convert.ToString(info["install"]):"";
                    string appId=info.ContainsKey("appId")?Convert.ToString(info["appId"]):"3879660";
                    Append(Loc.S("已识别 Steam 客户端（App ")+appId+"）"+(install.Length>0?"："+install:"")+Loc.S("。采集时将通过 Steam 启动，需先登录 Steam。"));
                }
                else Append(Loc.S("公服启动器路径已独立保存，私服游戏目录未改变。"));
                return true;
            }
            catch(Exception error) { string friendly=Friendly.Describe(error,"选择公服路径"); Append(friendly+"  "+Loc.S("详细原因：")+Friendly.Detail(error)); MessageBox.Show(this,friendly,Loc.S("公服路径不可用"),MessageBoxButtons.OK,MessageBoxIcon.Warning); return false; }
            finally { SetBusy(false, ""); }
        }
        async Task RefreshCaptureState()
        {
            if(checkingCaptureState || busy || closing || uiPreview) return;
            checkingCaptureState=true;
            try
            {
                var state=await Task.Run(() => backend.Official("status"));
                if(busy || closing) return;
                ApplyCaptureState(state);
            }
            catch(Exception error) { if(!closing) Append(Loc.S("采集状态检查失败：")+Friendly.Describe(error,"采集状态检查")); }
            // Must always release the guard: without this the first refresh (or the
            // first failure) latches the flag and no later state change can refresh.
            finally { checkingCaptureState=false; }
        }
        internal void ApplyCaptureState(Dictionary<string,object> state)
        {
            if (state == null || !state.ContainsKey("active")) throw new InvalidOperationException(Loc.S("采集状态结果不完整，请重试。"));
            captureRunning=Convert.ToBoolean(state["active"]);
            string zip=state.ContainsKey("accountZip")?Convert.ToString(state["accountZip"]):"";
            lastCaptureZip=File.Exists(zip)?zip:"";
            lastCaptureIncomplete=CaptureIncompleteWarning(state).Length>0;
            captureSaveZip.Enabled=!busy && !captureRunning && File.Exists(lastCaptureZip);
            officialBrowse.Enabled=!busy && !captureRunning;
            if(captureChannelBox!=null) captureChannelBox.Enabled=!busy && !captureRunning;
            if(captureDetectBtn!=null) captureDetectBtn.Enabled=!busy && !captureRunning;
            capturePreflight.Enabled=!busy && !captureRunning;
            captureStart.Enabled=!busy && !captureRunning;
            captureStop.Enabled=!busy && captureRunning;
        }
        void ShowCapturePage(bool show)
        {
            if (capturePage != null) capturePage.Visible = show;
            if (mainScroll != null && mainScroll.Controls.Count > 1)
                mainScroll.Controls[0].Visible = !show;
            if (show && captureHint != null && String.IsNullOrWhiteSpace(captureHint.Text))
                captureHint.Text = Loc.S("用你自己的渠道账号登录官方客户端，采集到的数据只写入本机账号库。");
            captureMode.Primary = show;
            soloMode.Primary = !show && backend.Mode == "solo";
            steamMode.Primary = !show && backend.Mode == "steam";
            hostMode.Primary = !show && backend.Mode == "host";
            captureMode.Invalidate(); soloMode.Invalidate(); steamMode.Invalidate(); hostMode.Invalidate();
            UpdateModeAccessibility();
        }
        void ApplyMode()
        {
            soloMode.Primary=backend.Mode=="solo"; steamMode.Primary=backend.Mode=="steam"; hostMode.Primary=backend.Mode=="host";
            soloMode.Invalidate(); steamMode.Invalidate(); hostMode.Invalidate();
            if(backend.Mode=="host") subtitle.Text=Loc.S("局域网服务端 · 按 UID 管理 · 支持 B服 / TapTap / Steam");
            else if(backend.Mode=="steam") subtitle.Text=Loc.S("独立存档 B · 支持 B服 / TapTap / Steam · 独立数据目录");
            else subtitle.Text=Loc.S("单机存档 A · 支持 B服 / TapTap / Steam · 独立数据目录");
            profileLabel.Text=Loc.S("独立数据目录  ·  ")+backend.ProfilePath;
            hostBox.Items.Clear();
            if(backend.Mode!="host") { hostBox.Items.Add(Loc.S("127.0.0.1（仅本机）")); hostBox.SelectedIndex=0; }
            else
            {
                foreach(string address in Backend.LanAddresses()) hostBox.Items.Add(address);
                if(!String.IsNullOrEmpty(backend.HostAddress) && !hostBox.Items.Contains(backend.HostAddress)) hostBox.Items.Add(backend.HostAddress);
                if(hostBox.Items.Contains(backend.HostAddress)) hostBox.SelectedItem=backend.HostAddress;
                else if(hostBox.Items.Count>0) { backend.HostAddress=hostBox.Items[0].ToString();hostBox.SelectedIndex=0; }
            }
            firewall.Enabled=backend.Mode=="host";
            services.Text=backend.Mode=="host" ? Loc.S("启动服务") : Loc.S("仅启动服务");
            uidBox.Value=backend.Uid;
            UpdateModeAccessibility();
        }
        async Task SwitchMode(string mode)
        {
            if(busy || closing || Array.IndexOf(Backend.KnownModes,mode)<0) return;
            string blocked=PrivateOperationBlockReason();
            if(blocked.Length>0) { Append(blocked); return; }
            if(backend.Mode==mode) { ShowCapturePage(false); ApplyMode(); return; }
            // Claim busy before any await: rapid clicks otherwise start several switch
            // chains at once, and their Stop/Refresh interleave into a stalled UI.
            busy = true;
            SetBusy(true, Loc.S("正在切换运行模式"));
            try
            {
                string previousMode=backend.Mode;
                var current=await Task.Run(() => backend.Status());
                if(closing) return;
                if(!StatusMatchesMode(previousMode,current)) throw new InvalidOperationException(Loc.S("服务状态与当前模式不一致，已取消切换。"));
                lastStatus=current;
                if(current.ClientRunning) { Append(Loc.S("请先退出游戏，再切换运行模式。")); return; }
                if(!String.IsNullOrEmpty(current.RuntimeMode))
                {
                    var stopped=await Task.Run(() => backend.Run("Stop"));
                    if(closing) return;
                    // Never change modes after a failed/incomplete stop.
                    OperationSuccessMessage("Stop",false,stopped,await Task.Run(() => backend.Status()),backend.Uid);
                }
                if(closing) return;
                backend.Mode=mode;
                try { backend.Save(); }
                catch { backend.Mode=previousMode; throw; }
                lastStatus=null; lastStatusError=null;
                ShowCapturePage(false); ApplyMode();
                Append(mode=="solo" ? Loc.S("已切换到单机，使用本电脑的独立单机存档。")
                     : mode=="steam" ? Loc.S("已切换到国际服（Steam），使用独立的英文存档。")
                     : Loc.S("已切换到局域网服务端，使用独立联机存档。"));
            }
            catch (Exception error) { Append(Loc.S("切换失败：") + Friendly.Describe(error,"切换模式")); AppendLogDetail(error); }
            finally
            {
                busy = false;
                if(!closing) SetBusy(false, "");
            }
            if(closing) return;
            await RefreshStatus();
        }
        static string FormatSectionList(object rawList)
        {
            var names = new List<string>();
            var list = rawList as System.Collections.IEnumerable;
            if (list != null && !(rawList is string))
            {
                foreach (object entry in list)
                {
                    string key = Convert.ToString(entry);
                    string label = key == "characters" ? "角色" : key == "bag" ? "背包" : key == "weapons" ? "武器" : key == "armor" ? "护甲" : key == "posters" ? "海报" : key == "outfits" ? "外观" : key == "garden" ? "家园" : key == "exploration" ? "探索" : key;
                    names.Add(Loc.S(label));
                }
            }
            return names.Count > 0 ? String.Join(" / ", names.ToArray()) : Loc.S("无");
        }
        uint PromptImportUid(uint fallbackUid)
        {
            using (var dlg = new Form())
            using (var prompt = new Label())
            using (var box = new NumericUpDown())
            using (var ok = new ActionButton(Loc.S("确定"), true))
            using (var cancel = new ActionButton(Loc.S("取消")))
            {
                dlg.Text = Loc.S("指定导入 UID");
                dlg.FormBorderStyle = FormBorderStyle.FixedDialog;
                dlg.StartPosition = FormStartPosition.CenterParent;
                dlg.MaximizeBox = false; dlg.MinimizeBox = false;
                dlg.ClientSize = new Size(380, 170);
                dlg.BackColor = Theme.Bg; dlg.ForeColor = Theme.Text;
                prompt.Text = Loc.S("该资源包中未包含玩家 UID（可为家园/部分数据包），请输入目标本地 UID：");
                prompt.SetBounds(20, 18, 340, 44);
                box.SetBounds(20, 68, 340, 28);
                box.Minimum = 1; box.Maximum = UInt32.MaxValue;
                box.Value = fallbackUid > 0 ? fallbackUid : 1000000;
                ok.SetBounds(160, 114, 96, 36);
                cancel.SetBounds(264, 114, 96, 36);
                ok.DialogResult = DialogResult.OK; cancel.DialogResult = DialogResult.Cancel;
                dlg.AcceptButton = ok; dlg.CancelButton = cancel;
                dlg.Controls.AddRange(new Control[] { prompt, box, ok, cancel });
                return dlg.ShowDialog(this) == DialogResult.OK ? (uint)box.Value : 0;
            }
        }
        async Task ImportCapture()
        {
            await ImportCapturePath(null);
        }
        async Task ImportCapturePath(string preselectedPath)
        {
            if(busy || closing) return;
            string blocked=PrivateOperationBlockReason();
            if(blocked.Length>0) { Append(blocked); return; }
            busy=true; SetBusy(true,Loc.S("正在准备导入"));
            try
            {
                var current=await Task.Run(() => backend.Status());
                if(closing) return;
                if(!StatusMatchesMode(backend.Mode,current)) throw new InvalidOperationException(Loc.S("服务状态与当前模式不一致，已取消导入。"));
                lastStatus=current;
                if(current.ClientRunning) { Append(Loc.S("请先退出游戏，再导入角色数据。")); return; }
                string path = preselectedPath;
                if (String.IsNullOrWhiteSpace(path))
                {
                    using(var dialog=new OpenFileDialog { Title=Loc.S("一键导入：选择资源包 / 账号ZIP / JSON / lolo.db（也可直接拖入窗口）"),Filter=Loc.S("一键导入支持包 (*.zip;*.json;*.db;*.sqlite)|*.zip;*.json;*.db;*.sqlite|账号备份 / 解码抓包 ZIP|*.zip|所有文件|*.*"),CheckFileExists=true })
                    { if(dialog.ShowDialog(this)!=DialogResult.OK) return; path=dialog.FileName; }
                }
                var info=await Task.Run(()=>backend.OneClickImport("plan",path,0)); if(closing) return;
                uint uid = info.ContainsKey("uid") ? Convert.ToUInt32(info["uid"]) : 0;
                if (uid == 0)
                {
                    uid = PromptImportUid(backend.Uid);
                    if (uid == 0) return;
                    info = await Task.Run(() => backend.OneClickImport("plan", path, uid));
                    if (closing) return;
                }
                bool conflict = info.ContainsKey("conflict") && Convert.ToBoolean(info["conflict"]);
                string verHint = info.ContainsKey("versionHint") ? Convert.ToString(info["versionHint"]) : "";
                string presentTxt = FormatSectionList(info.ContainsKey("presentSections") ? info["presentSections"] : null);
                string emptyTxt = FormatSectionList(info.ContainsKey("emptySections") ? info["emptySections"] : null);
                string message="UID "+uid+" · "+(info.ContainsKey("name") ? Convert.ToString(info["name"]) : "")
                    +(String.IsNullOrEmpty(verHint) ? "" : " ["+verHint+"]")
                    +Loc.S("\n等级 ")+(info.ContainsKey("level") ? Convert.ToString(info["level"]) : "1")
                    +" · "+(info.ContainsKey("characters") ? Convert.ToString(info["characters"]) : "0")+Loc.S(" 个角色")
                    +Loc.S("\n包含模块：")+presentTxt
                    +Loc.S("\n缺失留空：")+emptyTxt
                    +Loc.S("\n\n将导入当前模式的独立数据目录；缺失部分自动留空，已有数据会先备份，其他 UID 保留。");
                if (conflict) message += Loc.S("\n注意：本机已存在该 UID 的存档，继续将备份旧档后覆盖。");
                if(MessageBox.Show(this,message,Loc.S("确认导入资料"),MessageBoxButtons.OKCancel,MessageBoxIcon.Information)!=DialogResult.OK) return;
                if(!String.IsNullOrEmpty(current.RuntimeMode))
                {
                    var stopped=await Task.Run(() => backend.Run("Stop"));
                    OperationSuccessMessage("Stop",false,stopped,await Task.Run(() => backend.Status()),uid);
                }
                if(closing) return;
                SetBusy(true,Loc.S("正在导入角色资料"));
                var imported=await Task.Run(()=> { backend.PrepareProfile(); return backend.OneClickImport("import",path,uid,conflict); }); if(closing) return;
                string database=imported.ContainsKey("database")?Convert.ToString(imported["database"]):"";
                if(!imported.ContainsKey("uid") || Convert.ToUInt32(imported["uid"])!=uid || !File.Exists(database))
                    throw new InvalidOperationException(Loc.S("导入结果未确认写入目标存档，请查看导入报告。"));
                backend.Uid=uid; backend.Save(); uidBox.Value=uid;
                Append((conflict ? Loc.S("已覆盖更新数据库中的 UID ") : Loc.S("已导入 UID "))+uid+Loc.S(" 的资料。原数据已有备份，其他 UID 保留。"));
                AppendImportSummary(imported);
                Append(Loc.S("导入报告位于数据目录 accounts\\")+uid+"\\import-report.json。");
            }
            catch(Exception e) { Append(Loc.S("导入未完成：")+Friendly.Describe(e,"导入存档")); AppendLogDetail(e); }
            finally { SetBusy(false,""); }
            if(!closing) await RefreshStatus();
        }
        void OpenFolder(string path)
        {
            try { Directory.CreateDirectory(path); Process.Start(new ProcessStartInfo("explorer.exe", Backend.Quote(path)) { UseShellExecute = true }); }
            catch (Exception e) { Append(Loc.S("无法打开文件夹：") + Friendly.Describe(e,"打开文件夹")); }
        }
        void AppendImportSummary(Dictionary<string, object> imported)
        {
            if (imported != null && imported.ContainsKey("exploration"))
            {
                var exp = imported["exploration"] as Dictionary<string, object>;
                if (exp != null)
                {
                    int scenes = exp.ContainsKey("scenes") ? Convert.ToInt32(exp["scenes"]) : 0;
                    int items = exp.ContainsKey("collectionItems") ? Convert.ToInt32(exp["collectionItems"]) : 0;
                    int areas = exp.ContainsKey("areas") ? Convert.ToInt32(exp["areas"]) : 0;
                    int moons = exp.ContainsKey("moons") ? Convert.ToInt32(exp["moons"]) : 0;
                    Append(String.Format(Loc.S("已写入地图收集与区域解锁：{0} 个场景、{1} 条收集、{2} 个区域、{3} 个月亮。"), scenes, items, areas, moons));
                }
            }
            Append(Loc.S("提示：星云树、大地图总探索度百分比与任务等字段已随原始采集包保存在账号备份中，但当前私服服务端版本尚未实现其读取与下发。"));
        }
        void ReportError(string action, Exception error, bool showHint)
        {
            string friendly = Friendly.Describe(error, action);
            Append(friendly);
            AppendLogDetail(error);
            if (showHint && !closing) captureHint.Text = friendly;
            UpdateDynamicAccessibility();
        }
        void AppendLogDetail(Exception error)
        {
            string detail = Friendly.Detail(error);
            if (!String.IsNullOrWhiteSpace(detail))
                WriteLauncherLog("[" + DateTime.Now.ToString("HH:mm:ss") + "]  " + Loc.S("详细原因：") + detail + Environment.NewLine);
        }
        void WriteLauncherLog(string line)
        {
            try { Directory.CreateDirectory(Path.Combine(backend.BaseDir, "runtime")); File.AppendAllText(Path.Combine(backend.BaseDir, "runtime", "launcher.log"), line, new UTF8Encoding(false)); }
            catch { }
        }
        void Append(string message)
        {
            if (closing) return;
            string line = "[" + DateTime.Now.ToString("HH:mm:ss") + "]  " + message + Environment.NewLine;
            if (logs.TextLength > 45000) logs.Text = logs.Text.Substring(logs.TextLength - 25000);
            logs.AppendText(line); logs.SelectionStart = logs.TextLength; logs.ScrollToCaret();
            UpdateDynamicAccessibility();
            WriteLauncherLog(line);
        }
        void SetBusy(bool value, string text, bool cancellable = false)
        {
            if (closing) return;
            busy = value; operationCancellable = value && cancellable;
            start.Enabled = services.Enabled = !value; stop.Enabled = !value || cancellable;
            soloMode.Enabled=steamMode.Enabled=hostMode.Enabled=importData.Enabled=backupData.Enabled=!value;
            openData.Enabled=!value;
            captureMode.Enabled = !value;
            officialBrowse.Enabled = !value && !captureRunning;
            if(captureChannelBox!=null) captureChannelBox.Enabled = !value && !captureRunning;
            if(captureDetectBtn!=null) captureDetectBtn.Enabled = !value && !captureRunning;
            capturePreflight.Enabled = !value && !captureRunning;
            captureStart.Enabled = !value && !captureRunning;
            captureStop.Enabled = !value && captureRunning;
            captureSaveZip.Enabled = !value && !captureRunning && File.Exists(lastCaptureZip);
            uidBox.Enabled=!value && (lastStatus==null || !lastStatus.ClientRunning); hostBox.Enabled=!value && backend.Mode=="host" && (lastStatus==null || String.IsNullOrEmpty(lastStatus.RuntimeMode)); firewall.Enabled=!value && backend.Mode=="host";
            if(languageBox!=null) languageBox.Enabled=!value;
            roomsButton.Enabled=!value && !captureRunning && backend.Mode=="host" && lastStatus!=null && lastStatus.Ready;
            stop.Text = value ? (cancellable ? Loc.S("取消操作") : Loc.S("正在处理…")) : Loc.S("停止服务");
            bool canEditClient = !value && !captureRunning && (lastStatus == null || (!lastStatus.ServerRunning && !lastStatus.ProxyRunning));
            browse.Enabled = !value && lastStatus != null && !lastStatus.ServerRunning && !lastStatus.ProxyRunning;
            if(worldChannelBox!=null) worldChannelBox.Enabled = canEditClient;
            if(detectWorldClient!=null) detectWorldClient.Enabled = canEditClient;
            if (value) { badge.Text = text; badge.ForeColor = Theme.Accent; start.Text = Loc.S("正在处理…"); }
            else start.Text = StartButtonText(backend.Mode,lastStatus);
            UpdateDynamicAccessibility();
        }
        async Task RefreshStatus()
        {
            if (busy || checking || closing) return; checking = true;
            string requestedMode=backend.Mode;
            try
            {
                var status = await Task.Run(() => backend.Status());
                if (closing || busy) return;
                // A slow response from the previous mode must not repaint the new
                // mode's controls or make its services look ready.
                if(!TryApplyStatus(requestedMode,status)) return;
                lastStatusError = null;
            }
            catch (Exception e)
            {
                if (closing) return;
                lastStatus=null;
                server.UpdateState(false, Loc.S("无法检查，请查看运行记录")); gateway.UpdateState(false, Loc.S("等待检查")); proxy.UpdateState(false, Loc.S("等待检查"));
                badge.Text = Loc.S("需要检查配置"); badge.ForeColor = Color.FromArgb(244, 181, 125); browse.Enabled = true;
                string friendly=Friendly.Describe(e,"检查服务状态");
                if (lastStatusError != friendly) Append(Loc.S("状态检查失败：") + friendly); lastStatusError = friendly;
            }
            finally { checking = false; }
        }
        internal bool TryApplyStatus(string requestedMode, ServiceStatus status)
        {
            if(!StatusMatchesMode(requestedMode,status) || !StatusMatchesMode(backend.Mode,status)) return false;
            lastStatus=status; ApplyStatus(status); return true;
        }
        internal void ApplyStatus(ServiceStatus status)
        {
            if(status==null) throw new InvalidOperationException(Loc.S("无法读取服务状态。"));
            server.UpdateState(status.ServerRunning && status.HttpListening, status.ServerRunning && status.HttpListening ? Loc.S("运行中 · 本地服务正常") : Loc.S("未启动"));
            gateway.UpdateState(status.GatewayListening && status.ServerRunning, status.GatewayListening && status.ServerRunning ? Loc.S("已就绪 · 等待游戏连接") : Loc.S("等待服务启动"));
            proxy.UpdateState(status.ProxyRunning && status.ProxyListening && status.ProxyEnabled, status.ProxyRunning && status.ProxyListening && status.ProxyEnabled ? Loc.S("已连接 · 本地游戏线路") : status.ProxyRunning && status.ProxyListening ? Loc.S("代理设置未接入") : Loc.S("等待连接"));
            badge.Text = status.Ready ? (backend.Mode=="host" ? Loc.S("●  局域网已发布") : status.ClientRunning ? Loc.S("●  游戏已运行") : Loc.S("●  可以开始游戏")) : Loc.S("○  等待启动");
            badge.ForeColor = status.Ready ? Theme.Accent : Theme.Muted;
            start.Text = StartButtonText(backend.Mode,status);
            browse.Enabled = !captureRunning && !status.ServerRunning && !status.ProxyRunning;
            if(worldChannelBox!=null) worldChannelBox.Enabled = !busy && !captureRunning && !status.ServerRunning && !status.ProxyRunning;
            if(detectWorldClient!=null) detectWorldClient.Enabled = !busy && !captureRunning && !status.ServerRunning && !status.ProxyRunning;
            uidBox.Enabled=!captureRunning && !status.ClientRunning;
            hostBox.Enabled=!captureRunning && backend.Mode=="host" && String.IsNullOrEmpty(status.RuntimeMode);
            profileLabel.Text=Loc.S("独立数据目录  ·  ")+backend.ProfilePath;
            proxy.Caption=Loc.S("UID 与房间网关");
            proxy.UpdateState(status.ProxyRunning && status.ProxyEnabled,status.ProxyRunning && status.ProxyEnabled ? Loc.S("已就绪 · 独立账号与房间管理") : Loc.S("等待网关启动"));
            roomsButton.Enabled=!captureRunning && backend.Mode=="host" && status.Ready;
            if(backend.Mode=="host") gateway.UpdateState(status.GatewayListening,status.GatewayListening ? Loc.S("局域网地址 · ")+status.Target : Loc.S("等待发布局域网服务"));
            firewall.Text=backend.Mode=="host" && status.FirewallConfigured ? Loc.S("权限已配置") : Loc.S("局域网权限");
            firewall.Enabled=!captureRunning && backend.Mode=="host";
            UpdateDynamicAccessibility();
        }
        async Task Operate(string action, bool launch)
        {
            if (busy || closing) return;
            string blocked=PrivateOperationBlockReason();
            if(blocked.Length>0) { Append(blocked); return; }
            operationCancellation = new CancellationTokenSource();
            bool statusUpdated = false;
            SetBusy(true, action == "Stop" ? Loc.S("正在停止服务") : Loc.S("正在准备游戏"), true);
            Append(action == "Stop" ? Loc.S("正在停止当前模式的服务并恢复原网络路线。") : action=="Backup" ? Loc.S("正在备份独立存档…") : launch ? Loc.S("正在准备 UID ")+backend.Uid+Loc.S(" 的本地游戏，请稍候…") : Loc.S("正在启动 ")+(backend.Mode=="solo" ? Loc.S("单机") : backend.Mode=="steam" ? Loc.S("国际服") : Loc.S("局域网"))+Loc.S(" 服务，请稍候…"));
            try
            {
                var token = operationCancellation.Token;
                string mode=backend.Mode; uint uid=backend.Uid;
                var result = await Task.Run(() => {
                    if (action == "Start") { backend.ValidateRoot(backend.Root); if (!File.Exists(backend.Python)) throw new InvalidOperationException(Loc.S("未找到 Python，请在 launcher.settings.json 中设置 pythonExe。")); }
                    return backend.Run(action, launch, token);
                });
                if (closing) return;
                var status=await Task.Run(() => backend.Status());
                if(closing)return;
                if(!StatusMatchesMode(mode,status) || !StatusMatchesMode(backend.Mode,status)) throw new InvalidOperationException(Loc.S("操作结果与当前模式不一致，请重新检查状态。"));
                lastStatus=status; lastStatusError=null; ApplyStatus(status); statusUpdated = true;
                string message=OperationSuccessMessage(action,launch,result,status,uid);
                Append(message);
                if(action=="Start" && launch && status.ClientRunning) await Task.Run(() => backend.FocusClient());
            }
            catch (OperationCanceledException) { Append(Loc.S("操作已取消。已启动的服务会保留，请查看状态后重试。")); }
            catch (Exception e)
            {
                string actionLabel = action == "Stop" ? "停止服务" : action == "Backup" ? "备份存档" : "启动服务";
                string friendly = Friendly.Describe(e, actionLabel);
                Append(Loc.S("操作失败：") + friendly);
                AppendLogDetail(e);
                if (!uiPreview && smokePath == null && !closing)
                    MessageBox.Show(this, Friendly.WithOperationHint(actionLabel, friendly), Loc.S("操作失败"), MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
            finally
            {
                if (operationCancellation != null) { operationCancellation.Dispose(); operationCancellation = null; }
                SetBusy(false, "");
            }
            if (closing || statusUpdated) return;
            await RefreshStatus();
        }
        async void BrowseRoot(object sender, EventArgs e)
        {
            if (busy || checking) return;
            using (var dialog = new FolderBrowserDialog { Description = Loc.S("选择游戏安装目录，单机启动时使用其中的 launcher.exe"), SelectedPath = backend.Root, ShowNewFolderButton = false })
                if (dialog.ShowDialog(this) == DialogResult.OK)
                    try
                    {
                        backend.ValidateRoot(dialog.SelectedPath);
                        string foundClient = Backend.FindClient(dialog.SelectedPath);
                        if (foundClient == null) throw new InvalidOperationException(Loc.S("所选目录里没有找到游戏客户端 launcher.exe。\n请选择包含 OverField Game 或 launcher.exe 的目录。"));
                        string detected = Backend.DetectChannel(foundClient);
                        backend.Root = dialog.SelectedPath;
                        if (detected != "unknown")
                        {
                            backend.SetChannelRoot(detected, dialog.SelectedPath);
                            backend.Channel = detected;
                        }
                        backend.Save();
                        rootPath.Text = backend.Root;
                        PopulateChannelComboBoxes();
                        UpdateChannelBadge();
                        Append(Loc.S("游戏目录已保存。"));
                        await RefreshStatus();
                    }
                    catch (Exception ex) { string friendly=Friendly.Describe(ex,"选择游戏目录"); Append(friendly); AppendLogDetail(ex); MessageBox.Show(this, friendly, Loc.S("目录不可用"), MessageBoxButtons.OK, MessageBoxIcon.Warning); }
        }
        async Task Verify()
        {
            var report = new Dictionary<string, object>();
            try
            {
                backend.ValidateRoot(backend.Root); report["gameFiles"] = true;
                string gatewayDir = Path.GetDirectoryName(backend.AppFile("room_gateway.py"));
                var python = await Task.Run(() => backend.Execute(backend.Python, "-X utf8 -c " + Backend.Quote("import asyncio, sqlite3, sys; sys.path.insert(0, sys.argv[1]); import room_gateway; print('ok')") + " " + Backend.Quote(gatewayDir)));
                report["proxyDependency"] = python.Code == 0;
                var version = await Task.Run(() => backend.Execute(backend.Python, "-X utf8 " + Backend.Quote(backend.AppFile("repair_version.py")) + " --root " + Backend.Quote(backend.Root)));
                report["versionRead"] = version.Code == 0; report["version"] = version.Output;
                report["status"] = lastStatus; report["ready"] = lastStatus != null && lastStatus.Ready;
                report["timestamp"] = DateTime.Now.ToString("o");
            }
            catch (Exception e) { report["error"] = Friendly.Detail(e); }
            Directory.CreateDirectory(Path.Combine(backend.BaseDir, "runtime"));
            File.WriteAllText(Path.Combine(backend.BaseDir, "runtime", "launcher-verification.json"), new JavaScriptSerializer().Serialize(report), new UTF8Encoding(false));
            if (smokePath == null) Close();
        }
        void SaveSmoke()
        {
            try { using (var bitmap = new Bitmap(Width, Height)) { DrawToBitmap(bitmap, new Rectangle(0, 0, Width, Height)); bitmap.Save(smokePath, System.Drawing.Imaging.ImageFormat.Png); } }
            finally { Environment.Exit(0); }
        }
    }
}
