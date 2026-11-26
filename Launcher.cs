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
#if ENGLISH
[assembly: System.Reflection.AssemblyFileVersion("1.3.0.0")]
[assembly: System.Reflection.AssemblyInformationalVersion("1.3.0")]
[assembly: System.Reflection.AssemblyTitle("OverField International Launcher")]
[assembly: System.Reflection.AssemblyProduct("OverField International Launcher")]
#else
[assembly: System.Reflection.AssemblyFileVersion("1.3.0.0")]
[assembly: System.Reflection.AssemblyInformationalVersion("1.3.0")]
[assembly: System.Reflection.AssemblyTitle("开放空间私服启动器")]
[assembly: System.Reflection.AssemblyProduct("开放空间私服启动器")]
#endif

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
            // Separate names so the national and the international launcher can
            // both be open at once instead of one focusing the other.
            string instanceName = Loc.English ? "Local\\OverFieldLocalLauncherEN" : "Local\\OverFieldLocalLauncher";
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
                    if (previewDir != null)
                    {
                        // Preview artifacts are already written before teardown paint
                        // messages can fail in offscreen/headless sessions.
                        bool complete = Native.PreviewComplete(previewDir);
                        if (!complete) Native.WritePreviewError(previewDir, e.Exception);
                        Environment.Exit(complete ? 0 : 3);
                    }
                    MessageBox.Show(Loc.S("操作未完成：") + e.Exception.Message, Loc.S("开放空间启动器"), MessageBoxButtons.OK, MessageBoxIcon.Warning);
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
        public volatile bool CaptureStartInFlight;
        public string Python;
        public string Mode = Loc.English ? "steam" : "solo";
        public string HostAddress;
        public uint Uid = 1000000;
        // Each edition owns its settings file. Sharing one would let the English
        // launcher overwrite the Chinese launcher's game folder, UID and mode.
        public static readonly string[] KnownModes = { "solo", "host", "steam" };
        public static string SettingsName { get { return Loc.English ? "launcher.settings-en.json" : "launcher.settings.json"; } }
        public string ProfilePath { get { return Path.Combine(BaseDir, "profiles", Mode); } }
        public string ClientExe { get { return Path.Combine(ProfilePath, "client", "launcher.exe"); } }
        internal string ControlScript;
        internal int ControlTimeoutMilliseconds = 60000;
        readonly CancellationTokenSource lifetime = new CancellationTokenSource();
        public void CancelAll() { lifetime.Cancel(); }
        public Backend()
        {
            ControlScript = Path.Combine(BaseDir, "World-Control.ps1");
            var addresses = LanAddresses(); HostAddress = addresses.Count > 0 ? addresses[0] : "";
            Python = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Python", "Python312", "python.exe");
            var path = Path.Combine(BaseDir, SettingsName);
            if (File.Exists(path))
                try
                {
                    var data = new JavaScriptSerializer().Deserialize<Dictionary<string, string>>(File.ReadAllText(path, Encoding.UTF8));
                    if (data.ContainsKey("gameRoot")) Root = data["gameRoot"];
                    if (data.ContainsKey("officialLauncherPath")) OfficialLauncherPath = data["officialLauncherPath"];
                    if (data.ContainsKey("pythonExe")) Python = data["pythonExe"];
                    if (data.ContainsKey("mode") && Array.IndexOf(KnownModes, data["mode"]) >= 0) Mode = data["mode"];
                    if (data.ContainsKey("hostAddress") && !String.IsNullOrWhiteSpace(data["hostAddress"])) HostAddress = data["hostAddress"];
                    if (data.ContainsKey("uid")) { uint parsed; if (UInt32.TryParse(data["uid"], out parsed) && parsed > 0) Uid = parsed; }
                }
                catch { }
            string bundled = Path.Combine(BaseDir, "runtime-python", "python.exe");
            if (File.Exists(bundled)) Python = bundled;
            if (!File.Exists(Python))
                foreach (string folder in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'))
                    if (File.Exists(Path.Combine(folder, "python.exe"))) { Python = Path.Combine(folder, "python.exe"); break; }
        }
        public void Save()
        {
            var path = Path.Combine(BaseDir, SettingsName);
            var temp = path + ".tmp";
            File.WriteAllText(temp, new JavaScriptSerializer().Serialize(new Dictionary<string, string> { { "gameRoot", Root }, { "officialLauncherPath", OfficialLauncherPath }, { "pythonExe", Python }, { "mode", Mode }, { "hostAddress", HostAddress }, { "uid", Uid.ToString() } }), new UTF8Encoding(false));
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
        public Result Run(string action, bool launch = false, CancellationToken cancellation = default(CancellationToken))
        {            var arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File " + Quote(ControlScript) + " -Action " + action + " -GameRoot " + Quote(Root);
            arguments += " -Mode " + Mode + " -HostAddress " + Quote(Mode != "host" ? "127.0.0.1" : HostAddress) + " -Uid " + Uid.ToString();
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
                    process.StartInfo = new ProcessStartInfo(
                        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell", "v1.0", "powershell.exe"),
                        arguments)
                    { UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = BaseDir,
                      RedirectStandardOutput = true, RedirectStandardError = true,
                      StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8 };
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
        public Dictionary<string, object> Official(string action, string extra = "")
        {
            string script = Path.Combine(BaseDir, "official_capture.py");
            if (!File.Exists(script)) throw new InvalidOperationException(Loc.S("缺少 official_capture.py，请重新解压完整工具包。"));
            if(action == "start") extra += " --launcher " + Quote(OfficialLauncherPath) + " --launcher-managed";
            var arguments = "-X utf8 " + Quote(script) + " " + action + extra;
            int timeout = action == "stop" ? 1900000 : action == "start" ? 180000 : action == "preflight" ? 65000 : action == "export-zip" ? 120000 : 15000;
            var result = ExecuteProcess(Python, arguments, timeout, true, CancellationToken.None);
            if (result.Code != 0)
            {
                string message = result.Error.Length > 0 ? result.Error : result.Output;
                try
                {
                    var parsed = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(message);
                    if (parsed.ContainsKey("error")) message = Convert.ToString(parsed["error"]);
                }
                catch { }
                throw new InvalidOperationException(message.Length > 0 ? message : Loc.S("公服采集操作失败。"));
            }
            return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(result.Output);
        }
        public Dictionary<string, object> OfficialImport(string action, string accountZip, uint uid, bool replace, bool allowIncomplete = false)
        {
            string script = Path.Combine(BaseDir, "official_import.py");
            if (!File.Exists(script)) throw new InvalidOperationException(Loc.S("缺少 official_import.py，请重新解压完整工具包。"));
            var arguments = "-X utf8 " + Quote(script) + " " + action + " --file " + Quote(accountZip) + " --database " + Quote(Path.Combine(ProfilePath, "db", "lolo.db"));
            if (uid > 0) arguments += " --uid " + uid;
            if (replace) arguments += " --replace";
            if (allowIncomplete) arguments += " --allow-incomplete";
            var result = Execute(Python, arguments);
            if (result.Code != 0)
            {
                string message = result.Error.Length > 0 ? result.Error : result.Output;
                try
                {
                    var parsed = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(message);
                    if (parsed.ContainsKey("error")) message = Convert.ToString(parsed["error"]);
                }
                catch { }
                throw new InvalidOperationException(message.Length > 0 ? message : Loc.S("账号入库失败。"));
            }
            return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(result.Output);
        }
        public Dictionary<string, object> Capture(string action, string path, uint uid, bool replace)
        {
            string arguments = "-X utf8 " + Quote(Path.Combine(BaseDir, "capture_import.py")) + " " + action + " --file " + Quote(path) + " --profile " + Quote(ProfilePath) + " --uid " + uid.ToString();
            if (replace) arguments += " --replace";
            var result = Execute(Python, arguments);
            if (result.Code != 0) throw new InvalidOperationException(result.Error);
            return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(result.Output);
        }
        public void PrepareProfile()
        {
            var result = Execute(Python, "-X utf8 " + Quote(Path.Combine(BaseDir, "profile_manager.py")) + " prepare --root " + Quote(Root) + " --base " + Quote(Path.Combine(BaseDir, "profiles")) + " --mode " + Mode + " --host " + Quote(Mode != "host" ? "127.0.0.1" : HostAddress));
            if (result.Code != 0) throw new InvalidOperationException(result.Error);
        }
        public void FocusClient()
        {
            foreach (var process in Process.GetProcessesByName("launcher"))
                using (process) try { if (String.Equals(process.MainModule.FileName, ClientExe, StringComparison.OrdinalIgnoreCase) && process.MainWindowHandle != IntPtr.Zero) Native.Focus(process.MainWindowHandle); } catch { }
        }
        public void EnableLanFirewall()
        {
            var arguments = "-NoProfile -ExecutionPolicy Bypass -File " + Quote(Path.Combine(BaseDir, "Lan-Firewall.ps1")) + " -GameRoot " + Quote(Root) + " -HostAddress " + Quote(HostAddress) + " -ServerExe " + Quote(Path.Combine(BaseDir, "profiles", "host", "server", "Lolo.exe")) + " -PythonExe " + Quote(Python);
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
        readonly ActionButton soloMode = new ActionButton(Loc.S("单机模式")), steamMode = new ActionButton(Loc.S("国际服")), hostMode = new ActionButton(Loc.S("局域网服务")), captureMode = new ActionButton(Loc.S("公服采集")), importData = new ActionButton(Loc.S("导入数据")), backupData = new ActionButton(Loc.S("备份存档")), openData = new ActionButton(Loc.S("数据目录")), firewall = new ActionButton(Loc.S("局域网权限")), roomsButton=new ActionButton(Loc.S("房间与玩家"));
        readonly NumericUpDown uidBox = new NumericUpDown();
        readonly ComboBox hostBox = new ComboBox();
        readonly Label profileLabel = new Label(), modeHint = new Label();
        Surface capturePage;
        Panel mainScroll;
        readonly Label captureSteps = new Label();
        readonly Label headline = new Label(), subtitle = new Label(), badge = new Label(), footer = new Label();
        readonly TextBox rootPath = new TextBox();
        readonly RichTextBox logs = new RichTextBox();
        readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
        bool busy, checking, closing, operationCancellable;
        CancellationTokenSource operationCancellation;
        ServiceStatus lastStatus;
        string lastStatusError, smokePath;
        bool verify, uiPreview;
        string uiPreviewDir, uiPage="";
        public LauncherForm(string[] args)
        {
            Text = Loc.S("开放空间 · 本地启动器"); Font = Theme.Font(10); BackColor = Theme.Bg; ForeColor = Theme.Text;
            ClientSize = new Size(1240, 900); MinimumSize = new Size(1040, 740); StartPosition = FormStartPosition.CenterScreen;
            AutoScaleMode = AutoScaleMode.Dpi; DoubleBuffered = true;
            if (File.Exists(Path.Combine(backend.BaseDir, "launcher.ico"))) Icon = new Icon(Path.Combine(backend.BaseDir, "launcher.ico"));
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--smoke-test" && i + 1 < args.Length) smokePath = args[++i];
                else if (args[i] == "--verify") verify = true;
                else if (args[i] == "--ui-preview" && i + 1 < args.Length) { uiPreview=true;uiPreviewDir=args[++i]; }
                else if (args[i] == "--ui-mode" && i + 1 < args.Length) { string m=args[++i];if(Array.IndexOf(Backend.KnownModes,m)>=0)backend.Mode=m; }
                else if (args[i] == "--ui-page" && i + 1 < args.Length) { uiPage=args[++i]; }
                else if (args[i] == "--ui-size" && i + 1 < args.Length) { string[] s=args[++i].Split('x');int w,h;if(s.Length==2 && Int32.TryParse(s[0],out w) && Int32.TryParse(s[1],out h))ClientSize=new Size(Math.Max(1024,w),Math.Max(700,h)); }
            }
            if(uiPreview) { backend.HostAddress="192.168.1.10";backend.Uid=1000000;backend.Root=""; }
            BuildLayout();
            ConfigureAccessibility();
            ApplyMode();
            ShowCapturePage(false);
            soloMode.Click += async delegate { await SwitchMode("solo"); };
            steamMode.Click += async delegate { await SwitchMode("steam"); };
            captureMode.Click += async delegate { ShowCapturePage(true); if(!uiPreview) { await RefreshCaptureState(); await RefreshCaptureProgress(); } };
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
                } catch(Exception error) { Append(error.Message);MessageBox.Show(this,error.Message,Loc.S("采集预检查")); }
                finally { SetBusy(false, ""); }
            };
            captureStop.Enabled = false;
            captureSaveZip.Enabled = false;
            captureSaveZip.Click += async delegate {
                if(busy || captureRunning || !File.Exists(lastCaptureZip)) return;
                SetBusy(true, Loc.S("正在另存账号 ZIP"));
                try { await SaveCaptureZip(lastCaptureZip); }
                catch(Exception error) { Append(error.Message); captureHint.Text=error.Message; }
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
                    bool steam = Convert.ToString(result["officialKind"])=="steam";
                    Append(steam ? Loc.S("抓包已就绪，正在通过 Steam 启动客户端。公服路径未使用私服副本或本地UID。") : Loc.S("抓包已就绪，已启动配置的公服启动器。公服路径未使用私服副本或本地UID。"));
                    if (steam && result.ContainsKey("officialPid") && Convert.ToInt32(result["officialPid"])==0)
                        Append(Loc.S("Steam 正在启动游戏；如果它弹出登录或更新窗口，请先完成，进入游戏后开始加载要保存的界面。"));
                    if (result.ContainsKey("hint")) Append(Convert.ToString(result["hint"]));
                    captureHint.Text = Loc.S("采集中：公服启动器已打开，请正常登录并加载要保存的界面，完成后点「结束采集」。");
                }
                catch (Exception error) { Append(error.Message); captureHint.Text = error.Message; }
                finally { backend.CaptureStartInFlight=false; captureStartInFlight=false; SetBusy(false, ""); }
                await RefreshCaptureState();
            };
            captureStop.Click += async delegate
            {
                if (busy || !captureRunning) return;
                SetBusy(true, Loc.S("正在结束采集并生成账号 ZIP"));
                try
                {
                    var captured = await Task.Run(() => backend.Official("stop"));
                    captureRunning = false;
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
                    if(captured.ContainsKey("quality")) {
                        var quality=captured["quality"] as Dictionary<string,object>;
                        if(quality!=null && quality.ContainsKey("successful_decode_count")) Append(Loc.S("成功解码响应/消息数：")+Convert.ToString(quality["successful_decode_count"]));
                        if(quality!=null && quality.ContainsKey("trailing_bytes_count") && Convert.ToInt64(quality["trailing_bytes_count"])>0) Append(Loc.S("发现残缺流尾；请查看采集报告，不能视为完整备份。"));
                    }
                    if(captured.ContainsKey("exploration")) {
                        var exploration=captured["exploration"] as Dictionary<string,object>;
                        if(exploration!=null) Append(exploration.ContainsKey("evidence_available") && Convert.ToBoolean(exploration["evidence_available"])
                            ? Loc.S("探索观测报告已加入账号ZIP；总量未验证，不表示全图完整。")
                            : Loc.S("探索报告已生成，但尚未收到有效探索证据；未知不当0。"));
                    }
                    if(!await SaveCaptureZip(zip)) return;
                    uint loginUid = (uint)uidBox.Value;
                    var review = await Task.Run(() => backend.OfficialImport("plan", zip, loginUid, false));
                    bool conflict = review.ContainsKey("conflict") && Convert.ToBoolean(review["conflict"]);
                    if (conflict)
                    {
                        string detail = review.ContainsKey("conflictWith") ? Convert.ToString(review["conflictWith"]) : "";
                        Append(Loc.S("UID 冲突：") + detail + Loc.S(" 已有存档，未写入。请先备份该账号或换用其他 UID。"));
                        captureHint.Text = Loc.S("账号 ZIP 已交付；UID 冲突，未写入数据库：") + detail;
                        return;
                    }
                    string message = Loc.S("账号 ZIP 已保存交付。是否同时写入本机账号库？\nUID ") + Convert.ToString(review["uid"]) +
                        " · " + Convert.ToString(review["name"]) + Loc.S("\n等级 ") + Convert.ToString(review["level"]) +
                        " · " + Convert.ToString(review["characters"]) + Loc.S(" 个角色\n\n确认写入？");
                    bool incomplete = review.ContainsKey("quality") && Convert.ToBoolean(((Dictionary<string, object>)review["quality"])["incomplete"]);
                    if (incomplete)
                    {
                        var quality = (Dictionary<string, object>)review["quality"];
                        string detail = Loc.S("占位字段 ") + (Convert.ToInt32(quality["placeholderFields"]) + Convert.ToInt32(quality["characterPlaceholderFields"]))
                            + Loc.S(" 个，装备响应 ") + Convert.ToString(quality["equipmentResponses"]) + Loc.S(" 条");
                        message = Loc.S("这个包的解码不完整（") + detail + Loc.S("）。\n导入后外观与装备可能为空，但账号、等级、角色会写入。\n\n仍要导入吗？");
                        Append(Loc.S("注意：包体解码不完整，装备与外观可能为空。"));
                    }
                    if (captureWarning.Length > 0) message = captureWarning + "\n\n" + message;
                    if (MessageBox.Show(this, message, incomplete ? Loc.S("数据不完整，确认导入") : Loc.S("确认入库"), MessageBoxButtons.OKCancel, MessageBoxIcon.Warning) != DialogResult.OK) return;
                    if(!File.Exists(Path.Combine(backend.ProfilePath,"db","lolo.db")))
                        await Task.Run(() => backend.PrepareProfile());
                    var imported = await Task.Run(() => backend.OfficialImport("import", zip, loginUid, false, incomplete));
                    uint usedUid = Convert.ToUInt32(imported["uid"]);
                    backend.Uid = usedUid; backend.Save(); uidBox.Value = usedUid;
                    Append(Loc.S("账号已写入数据库：UID ") + usedUid + "（" + Convert.ToString(imported["name"]) + "）。");
                    captureHint.Text = Loc.S("已入库：UID ") + usedUid + Loc.S("。可用该 UID 登录当前模式。");
                }
                catch (Exception error) { Append(error.Message); captureHint.Text = error.Message; }
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
                catch (Exception e) { Append(e.Message); }
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
            captureProgressTimer.Interval=2500;
            captureProgressTimer.Tick+=async delegate { if(capturePage.Visible && !uiPreview) await RefreshCaptureProgress(); };
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
                closing = true; e.Cancel = true;
                timer.Stop(); timer.Dispose();
                captureProgressTimer.Stop(); captureProgressTimer.Dispose();
                backend.CancelAll();
                // Detached services would otherwise survive this process and keep
                // holding 18080/18100. Reap only what this launcher started.
                if (uiPreview || smokePath != null || e.CloseReason == CloseReason.WindowsShutDown)
                { Environment.Exit(0); return; }
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
        }
        void ConfigureAccessibility()
        {
            AccessibleName = Text;
            AccessibleDescription = Loc.S("选择运行模式，查看服务状态并管理本地游戏与存档。");
            logs.AccessibleName = Loc.S("运行记录"); logs.AccessibleDescription = Loc.S("只读操作与错误记录");
            rootPath.AccessibleName = Loc.S("游戏目录"); officialLauncherPath.AccessibleName = Loc.S("公服启动器路径");
            uidBox.AccessibleName = "UID"; hostBox.AccessibleName = Loc.S("服务地址");
            captureChecklist.AccessibleName = Loc.S("采集项目清单"); captureHint.AccessibleName = Loc.S("采集提示");
            captureProgressHint.AccessibleName = Loc.S("采集进度"); badge.AccessibleName = Loc.S("状态");
            profileLabel.AccessibleName = Loc.S("独立数据目录");
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
                string key=Loc.English && row.ContainsKey("messageEn")?"messageEn":"message";
                string message=row.ContainsKey(key)?Convert.ToString(row[key]):"";
                if(compact){message=message.Replace("\r","").Split('\n')[0];if(message.Length>240)message=message.Substring(0,240)+"…";}
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
        async Task RefreshCaptureProgress()
        {
            if(checkingCaptureProgress || closing || uiPreview) return;
            checkingCaptureProgress=true;
            try {
                var data=await Task.Run(() => backend.Official("progress"));
                if(closing) return;
                RenderCaptureProgress(data);
            } catch(Exception error) { if(!closing) captureProgressHint.Text=Loc.S("项目状态暂不可读：")+error.Message; }
            finally { checkingCaptureProgress=false; }
        }
        void RenderCaptureProgress(Dictionary<string,object> data)
        {
            captureChecklist.BeginUpdate();
            try {
                captureChecklist.Items.Clear();
                if(data.ContainsKey("items")) foreach(object item in (System.Collections.IEnumerable)data["items"]) {
                    var row=item as Dictionary<string,object>;if(row==null) continue;
                    string status=Convert.ToString(row["state"]);
                    var view=new ListViewItem(Loc.S(Convert.ToString(row["label"])));
                    view.SubItems.Add(Loc.S(Convert.ToString(row["status"])));view.SubItems.Add(Convert.ToString(row["count"]));view.SubItems.Add(Loc.S(Convert.ToString(row["hint"])));
                    view.ForeColor=status=="collected"?Color.FromArgb(36,128,100):(status=="missing" || status=="partial")?Color.FromArgb(168,99,38):Theme.Muted;
                    captureChecklist.Items.Add(view);
                }
                string warningKey=Loc.English && data.ContainsKey("warningEn")?"warningEn":"warning";
                string warning=data.ContainsKey(warningKey)?Loc.S(Convert.ToString(data[warningKey])):"";
                string stamp=data.ContainsKey("updatedAt")?Convert.ToString(data["updatedAt"]):"";
                object live=data.ContainsKey("livePackets")?data["livePackets"]:null;
                string packets=live!=null && Convert.ToInt64(live)>0 ? Loc.S(" · PktMon 已记录 ")+String.Format("{0:N0}",Convert.ToInt64(live))+Loc.S(" 个数据包") : "";
                captureProgressHint.Text=(String.IsNullOrWhiteSpace(warning)?Loc.S("最近核对：")+stamp:warning)+packets;
            } finally { captureChecklist.EndUpdate(); }
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
            } catch(Exception error) { Append(Loc.S("文件已保存，但无法打开所在位置：")+error.Message); }
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
                backend.OfficialLauncherPath=Convert.ToString(info["path"]);
                backend.Save(); officialLauncherPath.Text=backend.OfficialLauncherPath;
                if(info.ContainsKey("kind") && Convert.ToString(info["kind"])=="steam") {
                    string install=info.ContainsKey("install")?Convert.ToString(info["install"]):"";
                    Append(Loc.S("已识别 Steam 客户端（App ")+Convert.ToString(info["appId"])+"）"+(install.Length>0?"："+install:"")+Loc.S("。采集时将通过 Steam 启动，需先登录 Steam。"));
                }
                else Append(Loc.S("公服启动器路径已独立保存，私服游戏目录未改变。"));
                return true;
            }
            catch(Exception error) { Append(error.Message); MessageBox.Show(this,error.Message,Loc.S("公服路径不可用"),MessageBoxButtons.OK,MessageBoxIcon.Warning); return false; }
            finally { if(!alreadyBusy) SetBusy(false, ""); }
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
            catch(Exception error) { if(!closing) Append(Loc.S("采集状态检查失败：")+error.Message); }
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
        }
        void ApplyMode()
        {
            soloMode.Primary=backend.Mode=="solo"; steamMode.Primary=backend.Mode=="steam"; hostMode.Primary=backend.Mode=="host";
            soloMode.Invalidate(); steamMode.Invalidate(); hostMode.Invalidate();
            if(backend.Mode=="host") subtitle.Text=Loc.S("局域网服务端 · 按 UID 管理 · 独立联机存档");
            else if(backend.Mode=="steam") subtitle.Text=Loc.S("国际服单机 · Steam 客户端 · 英文界面 · 独立数据目录");
            else subtitle.Text=Loc.S("国服单机 · 官方中文客户端 · 独立数据目录");
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
            catch (Exception error) { Append(Loc.S("切换失败：") + error.Message); }
            finally
            {
                busy = false;
                if(!closing) SetBusy(false, "");
            }
            if(closing) return;
            await RefreshStatus();
        }
        async Task ImportCapture()
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
                string path;
                using(var dialog=new OpenFileDialog { Title=Loc.S("选择账号备份或解码抓包 ZIP"),Filter=Loc.S("账号备份 / 解码抓包 ZIP|*.zip"),CheckFileExists=true })
                { if(dialog.ShowDialog(this)!=DialogResult.OK) return; path=dialog.FileName; }
                var info=await Task.Run(()=>backend.Capture("inspect",path,0,false)); if(closing) return;
                uint uid=Convert.ToUInt32(info["uid"]);
                string message="UID "+uid+" · "+info["name"]+Loc.S("\n等级 ")+info["level"]+" · "+info["characters"]+Loc.S(" 个角色\n\n将导入当前模式的独立数据目录。已有数据会先备份；其他 UID 保留。\n运行中的服务会先停止，并断开在线玩家。\n角色、装备和背包会写入存档，尚未支持的模块会保留原始抓包数据。");
                if(MessageBox.Show(this,message,Loc.S("确认导入资料"),MessageBoxButtons.OKCancel,MessageBoxIcon.Information)!=DialogResult.OK) return;
                if(!String.IsNullOrEmpty(current.RuntimeMode))
                {
                    var stopped=await Task.Run(() => backend.Run("Stop"));
                    OperationSuccessMessage("Stop",false,stopped,await Task.Run(() => backend.Status()),uid);
                }
                if(closing) return;
                SetBusy(true,Loc.S("正在导入角色资料"));
                var imported=await Task.Run(()=> { backend.PrepareProfile(); return backend.Capture("import",path,uid,true); }); if(closing) return;
                string database=imported.ContainsKey("database")?Convert.ToString(imported["database"]):"";
                if(!imported.ContainsKey("uid") || Convert.ToUInt32(imported["uid"])!=uid || !File.Exists(database))
                    throw new InvalidOperationException(Loc.S("导入结果未确认写入目标存档，请查看导入报告。"));
                backend.Uid=uid; backend.Save(); uidBox.Value=uid;
                Append(Loc.S("已导入 UID ")+uid+Loc.S(" 的资料。原数据已有备份，其他 UID 保留。"));
                Append(Loc.S("导入报告位于数据目录 accounts\\")+uid+"\\import-report.json。");
            }
            catch(Exception e) { Append(Loc.S("导入未完成：")+e.Message); }
            finally { SetBusy(false,""); }
            if(!closing) await RefreshStatus();
        }
        void OpenFolder(string path)
        {
            try { Directory.CreateDirectory(path); Process.Start(new ProcessStartInfo("explorer.exe", Backend.Quote(path)) { UseShellExecute = true }); }
            catch (Exception e) { Append(Loc.S("无法打开文件夹：") + e.Message); }
        }
        void Append(string message)
        {
            if (closing) return;
            string line = "[" + DateTime.Now.ToString("HH:mm:ss") + "]  " + message + Environment.NewLine;
            if (logs.TextLength > 45000) logs.Text = logs.Text.Substring(logs.TextLength - 25000);
            logs.AppendText(line); logs.SelectionStart = logs.TextLength; logs.ScrollToCaret();
            try { Directory.CreateDirectory(Path.Combine(backend.BaseDir, "runtime")); File.AppendAllText(Path.Combine(backend.BaseDir, "runtime", "launcher.log"), line, new UTF8Encoding(false)); }
            catch { }
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
            capturePreflight.Enabled = !value && !captureRunning;
            captureStart.Enabled = !value && !captureRunning;
            captureStop.Enabled = !value && captureRunning;
            captureSaveZip.Enabled = !value && !captureRunning && File.Exists(lastCaptureZip);
            uidBox.Enabled=!value && (lastStatus==null || !lastStatus.ClientRunning); hostBox.Enabled=!value && backend.Mode=="host" && (lastStatus==null || String.IsNullOrEmpty(lastStatus.RuntimeMode)); firewall.Enabled=!value && backend.Mode=="host";
            roomsButton.Enabled=!value && !captureRunning && backend.Mode=="host" && lastStatus!=null && lastStatus.Ready;
            stop.Text = value ? (cancellable ? Loc.S("取消操作") : Loc.S("正在处理…")) : Loc.S("停止服务");
            browse.Enabled = !value && lastStatus != null && !lastStatus.ServerRunning && !lastStatus.ProxyRunning;
            if (value) { badge.Text = text; badge.ForeColor = Theme.Accent; start.Text = Loc.S("正在处理…"); }
            else start.Text = StartButtonText(backend.Mode,lastStatus);
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
                badge.Text = Loc.S("需要检查配置"); badge.ForeColor = Color.FromArgb(244, 181, 125);
                start.Text=StartButtonText(backend.Mode,null); browse.Enabled = true;
                uidBox.Enabled=false; hostBox.Enabled=false; roomsButton.Enabled=false; firewall.Enabled=false;
                if (lastStatusError != e.Message) Append(Loc.S("状态检查失败：") + e.Message); lastStatusError = e.Message;
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
            uidBox.Enabled=!captureRunning && !status.ClientRunning;
            hostBox.Enabled=!captureRunning && backend.Mode=="host" && String.IsNullOrEmpty(status.RuntimeMode);
            profileLabel.Text=Loc.S("独立数据目录  ·  ")+backend.ProfilePath;
            proxy.Caption=Loc.S("UID 与房间网关");
            proxy.UpdateState(status.ProxyRunning && status.ProxyEnabled,status.ProxyRunning && status.ProxyEnabled ? Loc.S("已就绪 · 独立账号与房间管理") : Loc.S("等待网关启动"));
            roomsButton.Enabled=!captureRunning && backend.Mode=="host" && status.Ready;
            if(backend.Mode=="host") gateway.UpdateState(status.GatewayListening,status.GatewayListening ? Loc.S("局域网地址 · ")+status.Target : Loc.S("等待发布局域网服务"));
            firewall.Text=backend.Mode=="host" && status.FirewallConfigured ? Loc.S("权限已配置") : Loc.S("局域网权限");
            firewall.Enabled=!captureRunning && backend.Mode=="host";
        }
        async Task Operate(string action, bool launch)
        {
            if (busy || closing) return;
            string blocked=PrivateOperationBlockReason();
            if(blocked.Length>0) { Append(blocked); return; }
            operationCancellation = new CancellationTokenSource();
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
                lastStatus=status; lastStatusError=null; ApplyStatus(status);
                string message=OperationSuccessMessage(action,launch,result,status,uid);
                Append(message);
                if(action=="Start" && launch && status.ClientRunning) await Task.Run(() => backend.FocusClient());
            }
            catch (OperationCanceledException) { if(!closing) { badge.Text=Loc.S("操作已取消");badge.ForeColor=Color.FromArgb(244,181,125);Append(Loc.S("操作已取消。已启动的服务会保留，请查看状态后重试。")); } }
            catch (Exception e) { if(!closing) { badge.Text=Loc.S("操作失败");badge.ForeColor=Color.FromArgb(244,181,125);Append(Loc.S("操作失败：") + e.Message); } }
            finally
            {
                if (operationCancellation != null) { operationCancellation.Dispose(); operationCancellation = null; }
                SetBusy(false, "");
            }
            if (closing) return;
            await RefreshStatus();
        }
        async void BrowseRoot(object sender, EventArgs e)
        {
            if (busy || closing) return;
            busy=true; SetBusy(true,Loc.S("正在检查游戏目录"));
            try
            {
                string selected;
                using (var dialog = new FolderBrowserDialog { Description = Loc.S("选择游戏安装目录，单机启动时使用其中的 launcher.exe"), SelectedPath = backend.Root, ShowNewFolderButton = false })
                { if(dialog.ShowDialog(this)!=DialogResult.OK) return; selected=dialog.SelectedPath; }
                await Task.Run(() => { backend.ValidateRoot(selected); if(Backend.FindClient(selected)==null) throw new InvalidOperationException(Loc.S("所选目录里没有找到游戏客户端 launcher.exe。\n请选择包含 OverField Game 或 launcher.exe 的目录。")); });
                if(closing)return;
                backend.Root=selected; backend.Save(); rootPath.Text=backend.Root; Append(Loc.S("游戏目录已保存。"));
            }
            catch (Exception ex) { if(!closing) { Append(ex.Message); MessageBox.Show(this,ex.Message,Loc.S("目录不可用"),MessageBoxButtons.OK,MessageBoxIcon.Warning); } }
            finally { busy=false; SetBusy(false,""); }
            if(!closing) await RefreshStatus();
        }
        async Task Verify()
        {
            var report = new Dictionary<string, object>();
            try
            {
                backend.ValidateRoot(backend.Root); report["gameFiles"] = true;
                var python = await Task.Run(() => backend.Execute(backend.Python, "-X utf8 -c " + Backend.Quote("import asyncio, sqlite3, room_gateway; print('ok')")));
                report["proxyDependency"] = python.Code == 0;
                var version = await Task.Run(() => backend.Execute(backend.Python, "-X utf8 " + Backend.Quote(Path.Combine(backend.BaseDir, "repair_version.py")) + " --root " + Backend.Quote(backend.Root)));
                report["versionRead"] = version.Code == 0; report["version"] = version.Output;
                report["status"] = lastStatus; report["ready"] = lastStatus != null && lastStatus.Ready;
                report["timestamp"] = DateTime.Now.ToString("o");
            }
            catch (Exception e) { report["error"] = e.Message; }
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
