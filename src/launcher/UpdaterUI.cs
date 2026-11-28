using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace OverFieldLocal
{
    partial class LauncherForm
    {
        // The Launcher FormClosing cleanup MUST be skipped when this is true.
        bool updaterHandoff;
        Label updaterStatus, updaterHintLabel;
        LinkLabel updaterCheck, updaterPause, updaterLater;
        System.Windows.Forms.Timer updaterTimer;
        bool updaterAllowed, updaterAutomatic = true, updaterWorking, updaterInitialized;
        bool updaterRecoveryRequired, updaterRecoveryRequested, updaterInstallRequested;
        DateTime updaterNextRecoveryProbe = DateTime.MinValue;
        string updaterStage, updaterVersion;
        DateTime updaterNextCheck = DateTime.MinValue, updaterPostpone = DateTime.MinValue;
        int updaterCountdown = -1;
        static string UText(string cn, string en) { return Loc.T(cn, en); }
        string UpdaterEdition { get { return "UNIFIED"; } }
        string UpdaterDirectory { get { return Path.Combine(backend.BaseDir, "runtime", "updates"); } }

        Control BuildUpdaterStrip()
        {
            var strip = Grid(4, 2);
            strip.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            strip.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 146));
            strip.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 134));
            strip.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 108));
            strip.RowStyles.Add(new RowStyle(SizeType.Absolute, 26));
            strip.RowStyles.Add(new RowStyle(SizeType.Absolute, 22));
            updaterStatus = FitLabel(UText("自动更新 · 就绪", "Auto update · Ready"), 8.5f, Theme.Muted);
            strip.Controls.Add(updaterStatus, 0, 0); strip.SetColumnSpan(updaterStatus, 4);
            updaterHintLabel = FitLabel(UText("仅更新启动器；游戏与存档不变", "Launcher only; games and saves unchanged"), 8, Theme.Muted);
            strip.Controls.Add(updaterHintLabel, 0, 1);
            updaterCheck = UpdaterLink(UText("检查更新", "Check now"));
            updaterPause = UpdaterLink(UText("暂停自动", "Pause auto"));
            updaterLater = UpdaterLink(UText("稍后更新", "Later"));
            strip.Controls.Add(updaterCheck, 1, 1); strip.Controls.Add(updaterPause, 2, 1); strip.Controls.Add(updaterLater, 3, 1);
            updaterCheck.LinkClicked += async delegate {
                if (updaterRecoveryRequired) {
                    if (!updaterAllowed || updaterWorking || closing) return;
                    updaterRecoveryRequested = true; updaterCountdown = -1;
                    SetUpdaterStatus(UText("已请求恢复更新 · 等待空闲（稍后设置仍生效）", "Recovery requested · Waiting for idle (Later remains respected)"));
                } else await CheckUpdater(true);
            };
            updaterPause.LinkClicked += delegate {
                updaterAutomatic = !updaterAutomatic; updaterCountdown = -1; updaterRecoveryRequested = false;
                updaterPause.Text = updaterAutomatic ? UText("暂停自动", "Pause auto") : UText("恢复自动", "Resume auto");
                SaveUpdaterSettings(); SetUpdaterStatus(updaterAutomatic ? UText("自动更新已恢复", "Automatic updates resumed") : UText("自动更新已暂停；已开始的下载可完成", "Paused; an active download may finish"));
            };
            updaterLater.LinkClicked += delegate { updaterPostpone = DateTime.UtcNow.AddMinutes(30); updaterCountdown = -1; SetUpdaterStatus(UText("已推迟30分钟；下载保留", "Deferred 30 minutes; download retained")); };
            return strip;
        }
        LinkLabel UpdaterLink(string text)
        {
            return new LinkLabel { Text = text, Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleRight, AutoSize = false,
                AutoEllipsis = true, Font = Theme.Font(8.5f), LinkColor = Theme.Muted, ActiveLinkColor = Theme.Accent, Margin = Padding.Empty };
        }
        void SetUpdaterStatus(string text) { if (!IsDisposed && updaterStatus != null) updaterStatus.Text = text; }
        void RefreshUpdaterLocale()
        {
            if (updaterHintLabel != null) updaterHintLabel.Text = UText("仅更新启动器；游戏与存档不变", "Launcher only; games and saves unchanged");
            if (updaterCheck != null) updaterCheck.Text = updaterRecoveryRequired ? UText("恢复更新", "Recover update") : UText("检查更新", "Check now");
            if (updaterPause != null) updaterPause.Text = updaterAutomatic ? UText("暂停自动", "Pause auto") : UText("恢复自动", "Resume auto");
            if (updaterLater != null) updaterLater.Text = UText("稍后更新", "Later");
            if (updaterStatus != null && !updaterAllowed) updaterStatus.Text = UText("开发 / 测试模式 · 更新已禁用", "Development / test mode · Updates disabled");
            else if (updaterStatus != null && !updaterWorking && updaterCountdown < 0 && updaterStage == null && !updaterRecoveryRequired)
                updaterStatus.Text = updaterAutomatic ? UText("自动更新 · 就绪", "Auto update · Ready") : UText("自动更新已暂停", "Automatic updates paused");
        }

        // Call once after argument parsing/BuildLayout, before showing the form.
        void InitializeUpdater(string[] args)
        {
            if (updaterInitialized) return; updaterInitialized = true;
            updaterAllowed = UpdaterEnvironmentAllowed(args, backend.BaseDir) && !uiPreview && !verify && smokePath == null;
            if (!updaterAllowed) {
                SetUpdaterStatus(UText("开发 / 测试模式 · 更新已禁用", "Development / test mode · Updates disabled"));
                updaterCheck.Enabled = updaterPause.Enabled = updaterLater.Enabled = false; return;
            }
            bool settingsError = false;
            try {
                string settings = Path.Combine(UpdaterDirectory, "ui-settings.json");
                if (!SafeUpdaterPath(settings)) throw new IOException("Unsafe update settings path");
                bool exists;
                try { File.GetAttributes(settings); exists = true; }
                catch (FileNotFoundException) { exists = false; }
                catch (DirectoryNotFoundException) { exists = false; }
                if (exists) updaterAutomatic = ParseUpdaterAutomatic(File.ReadAllText(settings));
            } catch (Exception e) {
                updaterAutomatic = false; settingsError = true;
                Append(UText("更新设置读取失败，自动更新已暂停：", "Update settings unavailable; automatic updates paused: ") + Friendly.Describe(e, UText("读取更新设置", "Reading update settings")));
            }
            updaterPause.Text = updaterAutomatic ? UText("暂停自动", "Pause auto") : UText("恢复自动", "Resume auto");
            // A stage downloaded by an earlier session must still be installable after a
            // restart; otherwise a paused launcher leaves the payload in staging forever.
            string resumed = ResumeUpdaterStage();
            string applied = ConsumeAppliedNotice();
            string idle = updaterAutomatic ? UText("自动更新 · 等待后台检查", "Auto update · Waiting for background check")
                                           : UText("自动更新已暂停", "Automatic updates paused");
            if (applied != null) idle = UText("已更新到 " + applied + " · 当前已是最新", "Updated to " + applied + " · Up to date");
            else if (resumed != null) idle = UText("已下载 " + resumed + " · 空闲后安装", "Downloaded " + resumed + " · installs when idle");
            SetUpdaterStatus(settingsError ? UText("更新设置损坏或不可读 · 已暂停自动更新", "Update settings invalid / unreadable · Auto updates paused") : idle);
            updaterTimer = new System.Windows.Forms.Timer { Interval = 1000 };
            updaterTimer.Tick += async delegate { await TickUpdater(); };
            Shown += delegate { updaterTimer.Start(); };
            FormClosed += delegate { updaterAllowed = false; updaterTimer.Stop(); updaterTimer.Dispose(); };
        }
        static string AppPath(string root, string name)
        {
            string nested = Path.Combine(root, "app", name);
            return File.Exists(nested) ? nested : Path.Combine(root, name);
        }
        internal static bool UpdaterEnvironmentAllowed(string[] args, string root)
        {
            string manifest = AppPath(root, "installation-manifest.json");
            string updater = AppPath(root, "launcher_updater.py");
            string apply = AppPath(root, "update_apply.py");
            if (!SafeUpdaterPath(root) || !SafeUpdaterPath(Path.Combine(root, "app")) ||
                !SafeUpdaterPath(Path.Combine(root, "runtime", "updates")) ||
                !SafeUpdaterPath(Path.Combine(root, "runtime", "updates", "ui-settings.json")) ||
                !SafeUpdaterPath(manifest) ||
                !SafeUpdaterPath(updater) ||
                !SafeUpdaterPath(apply) ||
                !File.Exists(manifest)) return false;
            foreach (string arg in args ?? new string[0]) {
                string flag = arg.ToLowerInvariant();
                if (flag.Contains("preview") || flag.Contains("smoke") || flag.Contains("verify") || flag.Contains("test")) return false;
            }
            return true;
        }
        void SaveUpdaterSettings()
        {
            if (!updaterAllowed) return;
            try { WriteUpdaterSettings(backend.BaseDir, updaterAutomatic); }
            catch (Exception e) { Append(UText("更新设置保存失败：", "Cannot save update settings: ") + Friendly.Describe(e, UText("保存更新设置", "Saving update settings"))); }
        }
        internal static bool ParseUpdaterAutomatic(string json)
        {
            var data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json);
            if (data == null || !data.ContainsKey("automatic") || !(data["automatic"] is bool)) throw new InvalidDataException("Expected boolean automatic in update settings");
            return (bool)data["automatic"];
        }
        internal static void WriteUpdaterSettings(string root, bool automatic)
        {
            string directory = Path.Combine(root, "runtime", "updates");
            string target = Path.Combine(directory, "ui-settings.json");
            string temporary = target + "." + Guid.NewGuid().ToString("N") + ".tmp";
            if (!SafeUpdaterPath(root) || !SafeUpdaterPath(directory) || !SafeUpdaterPath(target) || !SafeUpdaterPath(temporary)) throw new IOException("Unsafe update settings path");
            Directory.CreateDirectory(directory);
            try {
                if (!SafeUpdaterPath(target) || !SafeUpdaterPath(temporary)) throw new IOException("Unsafe update settings path");
                byte[] bytes = new UTF8Encoding(false).GetBytes(new JavaScriptSerializer().Serialize(new Dictionary<string, object> { { "automatic", automatic } }));
                using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None)) { stream.Write(bytes, 0, bytes.Length); stream.Flush(true); }
                if (!SafeUpdaterPath(target) || !SafeUpdaterPath(temporary)) throw new IOException("Unsafe update settings path");
                if (File.Exists(target)) File.Replace(temporary, target, null);
                else File.Move(temporary, target);
            } finally {
                // The unique absolute temp path was checked before creation; never remove a redirected path.
                if (SafeUpdaterPath(temporary) && File.Exists(temporary)) File.Delete(temporary);
            }
        }
        /// <summary>
        /// Read and consume the record the helper writes after a successful apply.
        /// Without this a finished update and a failed one look identical: the window
        /// simply reappears and the user cannot tell which happened.
        /// </summary>
        string ConsumeAppliedNotice()
        {
            try {
                string path = Path.Combine(UpdaterDirectory, "last-update.json");
                if (!SafeUpdaterPath(path) || !File.Exists(path)) return null;
                var data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(path));
                File.Delete(path);
                if (data == null || !data.ContainsKey("version")) return null;
                string version = Convert.ToString(data["version"]);
                string applied = Convert.ToString(data.ContainsKey("edition") ? data["edition"] : "");
                if (String.IsNullOrEmpty(version) || applied != UpdaterEdition) return null;
                return version;
            } catch (Exception) { return null; }
        }
        bool UpdaterLocallyIdle()
        {
            return updaterAllowed && !closing && !busy && !checking && !captureRunning && lastStatus != null && lastStatusError == null &&
                !lastStatus.ServerRunning && !lastStatus.ClientRunning && !lastStatus.GatewayListening &&
                !lastStatus.ProxyRunning && !lastStatus.ProxyListening && !lastStatus.HttpListening;
        }
        /// <summary>
        /// Real user activity that must stop the countdown: a game, services, a proxy
        /// listener or an active capture. Our own 5s status poll is NOT user activity.
        /// </summary>
        bool UpdaterExternalActivity()
        {
            return captureRunning || lastStatus == null || lastStatusError != null ||
                lastStatus.ServerRunning || lastStatus.ClientRunning || lastStatus.GatewayListening ||
                lastStatus.ProxyRunning || lastStatus.ProxyListening || lastStatus.HttpListening;
        }
        /// <summary>
        /// Work this window is doing right now (a status poll, a busy operation).
        /// It must pause the countdown for one tick, never restart it: the status
        /// poll runs every 5 seconds, so restarting would keep the countdown pinned.
        /// </summary>
        bool UpdaterInternalBusy()
        {
            return busy || checking;
        }
        /// <summary>
        /// Pure countdown step, so the launch decision can be tested without a window.
        /// externalActivity cancels (-1); internalBusy freezes the remaining value;
        /// a finished countdown returns 0 exactly once and asks the caller to launch.
        /// </summary>
        internal static int UpdaterCountdownStep(bool externalActivity, bool internalBusy, int countdown)
        {
            if (externalActivity) return -1;
            if (internalBusy) return countdown;
            if (countdown < 0) return -1;
            return countdown - 1 < 0 ? 0 : countdown - 1;
        }
        async Task TickUpdater()
        {
            if (!updaterAllowed || updaterWorking || closing || updaterHandoff) return;
            // Always inspect local recovery before version/network checks, even when auto is paused.
            if (updaterCountdown < 0 && DateTime.UtcNow >= updaterNextRecoveryProbe) {
                updaterWorking = true;
                try { await ReadUpdaterProbe(); updaterNextRecoveryProbe = DateTime.UtcNow.AddMinutes(5); }
                catch (Exception e) { UpdaterError(e); updaterNextRecoveryProbe = DateTime.UtcNow.AddMinutes(1); }
                finally { updaterWorking = false; }
                return;
            }
            bool recovery = updaterRecoveryRequired;
            bool requested = updaterRecoveryRequested || updaterInstallRequested;
            if (!UpdaterActionAuthorized(recovery, updaterAutomatic, requested, DateTime.UtcNow, updaterPostpone)) return;
            if (!recovery && updaterStage == null) {
                if (DateTime.UtcNow >= updaterNextCheck) await CheckUpdater(false);
                return;
            }
            if (DateTime.UtcNow < updaterPostpone) return;
            // Real user activity cancels the countdown. Our own 5s status poll does not:
            // resetting on it made the countdown restart at 10 before it ever reached 0.
            bool external = UpdaterExternalActivity(), internalBusy = UpdaterInternalBusy();
            if (external) {
                updaterCountdown = -1;
                SetUpdaterStatus(recovery ? UText("更新需要恢复 · 等待游戏 / 服务 / 采集空闲", "Update recovery needed · Waiting for game / services / capture") : UText("更新已下载 · 等待游戏 / 服务 / 采集空闲", "Downloaded · Waiting for game / services / capture to finish"));
                return;
            }
            // A poll or short operation in flight: keep the countdown frozen for this tick.
            if (internalBusy) return;
            if (updaterCountdown < 0) {
                updaterWorking = true;
                try { if (await ProbeUpdaterIdle(recovery) && UpdaterActionAuthorized(recovery, updaterAutomatic, updaterRecoveryRequested || updaterInstallRequested, DateTime.UtcNow, updaterPostpone)) updaterCountdown = 10; }
                catch (Exception e) { UpdaterError(e); updaterPostpone = DateTime.UtcNow.AddMinutes(5); }
                finally { updaterWorking = false; }
                return;
            }
            SetUpdaterStatus((recovery ? UText("恢复更新 · 空闲重启倒计时 ", "Recover update · Restart while idle in ") : UText("更新已就绪 · 空闲重启倒计时 ", "Update ready · Restart while idle in ")) + updaterCountdown + UText(" 秒（可稍后）", "s (Later to defer)"));
            int next = UpdaterCountdownStep(external, internalBusy, updaterCountdown);
            if (next != 0) { updaterCountdown = next; return; }
            updaterWorking = true;
            try {
                if (!await ProbeUpdaterIdle(recovery) || UpdaterExternalActivity() || !UpdaterActionAuthorized(recovery, updaterAutomatic, updaterRecoveryRequested || updaterInstallRequested, DateTime.UtcNow, updaterPostpone)) { updaterCountdown = -1; return; }
                // Never trust or execute a URL or program from a download event. Only the installed, fixed helper is launched.
                if (!recovery && !ValidUpdaterStage(backend.BaseDir, updaterVersion, UpdaterEdition, updaterStage)) throw new InvalidOperationException("Invalid update staging path");
                string arguments = (recovery ? "launch-recover" : "launch") + " --root " + Backend.Quote(backend.BaseDir.TrimEnd('\\', '/')) +
                    (recovery ? "" : " --stage " + Backend.Quote(updaterStage) + " --edition " + UpdaterEdition) + " --parent-pid " + Process.GetCurrentProcess().Id + " --restart";
                // The installed launch handshake records our parent identity before spawning apply.
                // A raw CreateProcess(apply) followed by Close can lose that identity during Python imports.
                var launch = await Task.Run(() => UpdaterNative.Start(backend.Python, backend.AppFile("update_apply.py"), arguments, backend.BaseDir, UpdaterDirectory, true, 15000));
                string launchStatus = UpdaterValue(launch, "status");
                if (launchStatus == "busy" || launchStatus == "unknown" || launchStatus == "recovery_required") {
                    updaterCountdown = -1; updaterPostpone = DateTime.UtcNow.AddSeconds(15);
                    SetUpdaterStatus(recovery ? UText("更新需要恢复 · 等待空闲或状态确认", "Recovery needed · Waiting for idle / confirmed status") : UText("更新已下载 · 等待空闲或状态确认", "Downloaded · Waiting for idle / confirmed status"));
                    return;
                }
                if (launchStatus == "conflict") {
                    updaterCountdown = -1; updaterInstallRequested = false; updaterRecoveryRequested = false; updaterStage = null; updaterAutomatic = false;
                    updaterPause.Text = UText("恢复自动", "Resume auto");
                    SaveUpdaterSettings();
                    updaterPostpone = DateTime.UtcNow.AddHours(6);
                    string reason = UpdaterValue(launch, "reason");
                    string details = UpdaterDetails(launch);
                    if (reason == "local_changes") {
                        SetUpdaterStatus(UText("检测到本地受管文件已修改 · 自动更新已暂停并保留修改", "Local managed files edited · Auto-update paused (edits kept)"));
                        Append(UText("自动更新：检测到本地受管文件已修改，已暂停并保留修改：", "Auto update: local managed files edited; paused and preserved edits: ") + details);
                    } else {
                        SetUpdaterStatus(UText("更新基线缺失或版本不兼容 · 自动更新已暂停", "Installation baseline missing or incompatible · Auto-update paused"));
                        Append(UText("自动更新：当前安装基线与更新包不匹配，已暂停自动更新（原版本可继续使用）。", "Auto update: installation baseline does not match update package; automatic updates paused."));
                    }
                    return;
                }
                if (launchStatus != "launched") throw new InvalidOperationException("Update handoff was not acknowledged");
                if (UpdaterExternalActivity() || !UpdaterActionAuthorized(recovery, updaterAutomatic, updaterRecoveryRequested || updaterInstallRequested, DateTime.UtcNow, updaterPostpone)) {
                    // The waiting apply helper times out without killing us or applying while the parent lives.
                    updaterCountdown = -1; updaterPostpone = DateTime.UtcNow.AddMinutes(3); return;
                }
                updaterHandoff = true; Close();
            } catch (Exception e) { updaterCountdown = -1; updaterPostpone = DateTime.UtcNow.AddMinutes(5); UpdaterError(e); }
            finally { updaterWorking = false; }
        }
        async Task CheckUpdater(bool manual)
        {
            if (!updaterAllowed || closing || updaterWorking || updaterHandoff || updaterRecoveryRequired || (!manual && !updaterAutomatic)) return;
            updaterWorking = true; updaterCheck.Enabled = false; updaterCountdown = -1;
            if (manual) updaterPostpone = DateTime.MinValue;
            updaterNextCheck = DateTime.UtcNow.AddHours(6);
            SetUpdaterStatus(UText("正在后台检查并下载正式版…", "Checking / downloading stable release in background…"));
            try {
                await ReadUpdaterProbe();
                if (updaterRecoveryRequired || closing || IsDisposed) return;
                var fileVersion = new Version(FileVersionInfo.GetVersionInfo(Assembly.GetExecutingAssembly().Location).FileVersion);
                string version = fileVersion.Major + "." + fileVersion.Minor + "." + Math.Max(0, fileVersion.Build);
                string arguments = "check --root " + Backend.Quote(backend.BaseDir.TrimEnd('\\', '/')) + " --edition " + UpdaterEdition + " --current-version " + Backend.Quote(version) + (manual ? " --force" : "") + " --events";
                var result = await Task.Run(() => UpdaterNative.Start(backend.Python, backend.AppFile("launcher_updater.py"), arguments, backend.BaseDir, UpdaterDirectory, true, 20 * 60 * 1000));
                if (closing || IsDisposed) return;
                string status = UpdaterValue(result, "status");
                if (status == "downloaded") {
                    string stage = UpdaterValue(result, "stage_dir"), nextVersion = UpdaterValue(result, "version");
                    if (!ValidUpdaterStage(backend.BaseDir, nextVersion, UpdaterEdition, stage)) throw new InvalidOperationException("Invalid update staging path");
                    updaterStage = Path.GetFullPath(stage); updaterVersion = nextVersion;
                    // A manual check is an explicit request to install, even while paused.
                    if (manual) updaterInstallRequested = true;
                    SetUpdaterStatus(UText("正式版已下载 · 等待空闲安全重启", "Stable release downloaded · Waiting for a safe idle restart"));
                } else if (status == "up_to_date") SetUpdaterStatus(UText("已是最新正式版", "Latest stable release installed"));
                else if (status == "deferred") SetUpdaterStatus(UText("更新检查已推迟 · 稍后可重试", "Update deferred · Retry later"));
                else throw new InvalidOperationException("Update check failed; see updater logs");
            } catch (Exception e) { UpdaterError(e); updaterNextCheck = DateTime.UtcNow.AddMinutes(30); }
            finally { updaterWorking = false; if (!IsDisposed) updaterCheck.Enabled = updaterAllowed; }
        }
        /// <summary>
        /// Re-attach to a stage left by an earlier run. Returns its version, or null.
        /// Only a complete, self-consistent stage with a valid signature folder is used;
        /// the independent apply helper revalidates everything again before touching files.
        /// </summary>
        string ResumeUpdaterStage()
        {
            try {
                string state = Path.Combine(UpdaterDirectory, "update-state.json");
                if (!SafeUpdaterPath(state) || !File.Exists(state)) return null;
                string text = File.ReadAllText(state);
                if (text.Length > 8192) return null;
                var data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(text);
                if (data == null || UpdaterValue(data, "status") != "downloaded") return null;
                string version = UpdaterValue(data, "version"), edition = UpdaterValue(data, "edition");
                string stage = UpdaterValue(data, "stage_dir");
                if (edition != UpdaterEdition) return null;
                if (!ValidUpdaterStage(backend.BaseDir, version, edition, stage)) return null;
                string full = Path.GetFullPath(stage);
                if (!File.Exists(Path.Combine(full, "payload.zip")) || !File.Exists(Path.Combine(full, "manifest.json")) ||
                    !File.Exists(Path.Combine(full, "manifest.sig"))) return null;
                var local = new Version(FileVersionInfo.GetVersionInfo(Assembly.GetExecutingAssembly().Location).FileVersion);
                var target = new Version(version);
                if (target <= local) return null;
                updaterStage = full; updaterVersion = version;
                // Resuming a downloaded stage is an explicit intent to install it.
                updaterInstallRequested = true;
                return version;
            } catch { return null; }
        }
        internal static bool UpdaterActionAuthorized(bool recovery, bool automatic, bool requested, DateTime now, DateTime postpone)
        {
            // A paused launcher must never install on its own, but an explicit user
            // request (manual "Check now" that downloaded a stage, or manual recovery)
            // still has to be able to finish. Otherwise the download sits in staging
            // forever and the strip keeps reporting the update as unavailable.
            if (now < postpone) return false;
            return automatic || requested;
        }
        async Task<Dictionary<string, object>> ReadUpdaterProbe()
        {
            var result = await Task.Run(() => UpdaterNative.Start(backend.Python, backend.AppFile("update_apply.py"),
                "probe --root " + Backend.Quote(backend.BaseDir.TrimEnd('\\', '/')) + " --ignore-pid " + Process.GetCurrentProcess().Id, backend.BaseDir, UpdaterDirectory, true, 15000));
            if (closing || IsDisposed) return result;
            string status = UpdaterValue(result, "status");
            if (status == "recovery_required") {
                if (!updaterRecoveryRequired) updaterCountdown = -1;
                updaterRecoveryRequired = true; updaterCheck.Text = UText("恢复更新", "Recover update");
                SetUpdaterStatus(UText("检测到未完成更新 · 等待空闲恢复，可手动恢复或稍后", "Unfinished update detected · Recover when idle, manually, or Later"));
            } else if (status == "idle" && updaterRecoveryRequired) {
                updaterRecoveryRequired = updaterRecoveryRequested = false; updaterCountdown = -1;
                updaterCheck.Text = UText("检查更新", "Check now");
            }
            return result;
        }
        internal static bool UpdaterProbeAllowsAction(Dictionary<string, object> result, bool recovery)
        {
            if (UpdaterValue(result, "status") != (recovery ? "recovery_required" : "idle")) return false;
            object busyItems;
            if (!result.TryGetValue("busy", out busyItems)) return false;
            var items = busyItems as System.Collections.IList;
            return items != null && items.Count == 0;
        }
        async Task<bool> ProbeUpdaterIdle(bool recovery = false)
        {
            var result = await ReadUpdaterProbe();
            bool idle = UpdaterProbeAllowsAction(result, recovery);
            if (!idle) {
                updaterCountdown = -1;
                if (DateTime.UtcNow.AddSeconds(15) > updaterPostpone) updaterPostpone = DateTime.UtcNow.AddSeconds(15);
                SetUpdaterStatus(updaterRecoveryRequired ? UText("更新需要恢复 · 等待空闲或状态确认", "Recovery needed · Waiting for idle / confirmed status") : UText("更新已下载 · 等待空闲或状态确认", "Downloaded · Waiting for idle / confirmed status"));
            }
            return idle;
        }
        void UpdaterError(Exception e) { SetUpdaterStatus(UText("更新暂不可用 · 原版本可继续使用", "Update unavailable · Current version remains usable")); if (!closing) Append(UText("自动更新：", "Auto update: ") + Friendly.Describe(e, UText("自动更新", "Auto update"))); }
        static string UpdaterValue(Dictionary<string, object> data, string key) { return data != null && data.ContainsKey(key) ? Convert.ToString(data[key]) : ""; }
        static string UpdaterDetails(Dictionary<string, object> data)
        {
            object raw;
            if (data == null || !data.TryGetValue("details", out raw) || raw == null) return "";
            var list = raw as System.Collections.IEnumerable;
            if (list == null || raw is string) return Convert.ToString(raw);
            var items = new List<string>();
            foreach (object item in list) {
                string text = Convert.ToString(item);
                if (!String.IsNullOrWhiteSpace(text)) items.Add(text);
                if (items.Count >= 6) break;
            }
            return String.Join(", ", items.ToArray());
        }
        // Check every existing component, including files and dangling directory links.
        // Missing components are allowed so a safe runtime directory can be created.
        internal static bool SafeUpdaterPath(string path)
        {
            try {
                string current = Path.GetFullPath(path);
                while (!String.IsNullOrEmpty(current)) {
                    try { if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0) return false; }
                    catch (FileNotFoundException) { }
                    catch (DirectoryNotFoundException) { }
                    var parent = Directory.GetParent(current);
                    current = parent == null ? null : parent.FullName;
                }
                return true;
            } catch { return false; }
        }
        internal static bool ValidUpdaterStage(string root, string version, string edition, string stage)
        {
            if (String.IsNullOrWhiteSpace(version) || String.IsNullOrWhiteSpace(stage) || edition != "UNIFIED") return false;
            foreach (char c in version) if (!(Char.IsDigit(c) || c == '.')) return false;
            try {
                string expected = Path.GetFullPath(Path.Combine(root, "runtime", "updates", "staging", version + "-" + edition));
                string actual = Path.GetFullPath(stage).TrimEnd('\\', '/');
                if (!String.Equals(expected, actual, StringComparison.OrdinalIgnoreCase)) return false;
                // Reject existing reparse-point ancestors; independent apply revalidates all contents/signatures.
                return SafeUpdaterPath(actual);
            } catch { return false; }
        }
    }

    // CreateProcess assigns three real file handles, not redirected anonymous pipes.
    // In particular the apply helper can outlive the form without keeping its stdout reader alive.
    internal static class UpdaterNative
    {
        [StructLayout(LayoutKind.Sequential)] struct Security { public int size; public IntPtr descriptor; public int inherit; }
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] struct Startup {
            public int cb; public string reserved, desktop, title; public int x,y,xSize,ySize,xChars,yChars,fill,flags; public short show,reservedSize; public IntPtr reserved2,input,output,error;
        }
        [StructLayout(LayoutKind.Sequential)] struct Info { public IntPtr process, thread; public int pid, tid; }
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateFile(string name,uint access,uint share,ref Security security,uint creation,uint flags,IntPtr template);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool CreateProcess(string application,StringBuilder command,IntPtr processSecurity,IntPtr threadSecurity,bool inherit,uint flags,IntPtr environment,string directory,ref Startup startup,out Info info);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle,uint milliseconds);
        [DllImport("kernel32.dll")] static extern bool TerminateProcess(IntPtr handle,uint code);
        [DllImport("kernel32.dll")] static extern bool GetExitCodeProcess(IntPtr handle,out uint code);
        static bool Invalid(IntPtr handle) { return handle == IntPtr.Zero || handle == new IntPtr(-1); }
        internal static Dictionary<string,object> Start(string python,string script,string arguments,string root,string directory,bool wait,int timeout)
        {
            if (!LauncherForm.SafeUpdaterPath(root) || !LauncherForm.SafeUpdaterPath(directory) || !LauncherForm.SafeUpdaterPath(script)) throw new IOException("Unsafe installed helper or log directory path");
            if (!File.Exists(python) || !File.Exists(script)) throw new FileNotFoundException("Installed update helper or Python missing");
            Directory.CreateDirectory(directory);
            string stem = Path.Combine(directory,"gui-"+DateTime.UtcNow.ToString("yyyyMMdd-HHmmss")+"-"+Guid.NewGuid().ToString("N"));
            string stdout = stem + ".stdout.jsonl", stderr = stem + ".stderr.log";
            if (!LauncherForm.SafeUpdaterPath(stdout) || !LauncherForm.SafeUpdaterPath(stderr)) throw new IOException("Unsafe update log file path");
            var security = new Security { size = Marshal.SizeOf(typeof(Security)), inherit = 1 };
            IntPtr input=IntPtr.Zero, output=IntPtr.Zero, error=IntPtr.Zero; var info = new Info();
            try {
                input=CreateFile("NUL",0x80000000,3,ref security,3,0,IntPtr.Zero);
                output=CreateFile(stdout,0x40000000,3,ref security,2,0,IntPtr.Zero);
                error=CreateFile(stderr,0x40000000,3,ref security,2,0,IntPtr.Zero);
                if (Invalid(input)||Invalid(output)||Invalid(error)) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                var startup = new Startup { cb=Marshal.SizeOf(typeof(Startup)), flags=0x100, input=input, output=output, error=error };
                var command = new StringBuilder(Backend.Quote(python)+" -X utf8 "+Backend.Quote(script)+" "+arguments);
                if (!CreateProcess(python,command,IntPtr.Zero,IntPtr.Zero,true,0x08000000,IntPtr.Zero,root,ref startup,out info)) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                if (!wait) return new Dictionary<string,object>{{"status","started"}};
                uint outcome=WaitForSingleObject(info.process,(uint)timeout);
                if (outcome!=0) { TerminateProcess(info.process,124); throw new TimeoutException("Update helper timed out; logs: "+stderr); }
                uint code; GetExitCodeProcess(info.process,out code);
                // Release our writable handles before File.ReadLines opens with FileShare.Read.
                CloseHandle(output); output=IntPtr.Zero; CloseHandle(error); error=IntPtr.Zero;
                // Bound parsing, ignore progress events; final status is the protocol result.
                // Read with shared write access: the helper may still hold the log briefly
                // after exiting, and an exclusive open would surface as "file in use".
                if (new FileInfo(stdout).Length > 4*1024*1024) throw new InvalidDataException("Update event log too large: "+stdout);
                Dictionary<string,object> final=null;
                using (var reader = new StreamReader(new FileStream(stdout, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete), Encoding.UTF8)) {
                    string line;
                    while ((line = reader.ReadLine()) != null) {
                        try { var row=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(line); if(row!=null && row.ContainsKey("status")) final=row; } catch (ArgumentException) { }
                    }
                }
                if (final==null) throw new InvalidDataException("No update helper result; exit="+code+"; logs: "+stderr);
                string finalStatus = Convert.ToString(final["status"]);
                if (code!=0 && finalStatus!="busy" && finalStatus!="unknown" && finalStatus!="recovery_required" && finalStatus!="deferred" && finalStatus!="conflict") {
                    object msgObj;
                    if (final.TryGetValue("message", out msgObj) && !String.IsNullOrWhiteSpace(Convert.ToString(msgObj)))
                        throw new InvalidOperationException(Convert.ToString(msgObj));
                    if (final.TryGetValue("reason", out msgObj) && !String.IsNullOrWhiteSpace(Convert.ToString(msgObj)))
                        throw new InvalidOperationException(Convert.ToString(msgObj));
                    throw new InvalidDataException("Update helper failed; exit="+code+"; logs: "+stderr);
                }
                return final;
            } finally {
                if(!Invalid(input))CloseHandle(input); if(!Invalid(output))CloseHandle(output); if(!Invalid(error))CloseHandle(error);
                if(!Invalid(info.thread))CloseHandle(info.thread); if(!Invalid(info.process))CloseHandle(info.process);
            }
        }
    }
}
