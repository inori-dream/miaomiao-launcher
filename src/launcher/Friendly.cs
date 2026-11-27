// Turns technical exceptions into one short, actionable sentence.
// The raw exception still goes to the run log, so support keeps full detail
// while the window never shows stack traces, paths or English framework text.
using System;
using System.ComponentModel;
using System.IO;
using System.Net;
using System.Text;
using System.Threading.Tasks;

namespace OverFieldLocal
{
    static class Friendly
    {
        /// <summary>Short cause + what to do. The caller logs the raw error separately.</summary>
        public static string Describe(Exception error, string action)
        {
            if (error == null) return Loc.S("发生未预期错误，详细原因已写入运行记录；可点「打开日志文件夹」查看。");
            if (String.IsNullOrEmpty(action)) action = Loc.S("操作");
            while ((error is AggregateException || error is System.Reflection.TargetInvocationException)
                   && error.InnerException != null) error = error.InnerException;

            if (error is OperationCanceledException || error is TaskCanceledException)
                return Loc.S("操作已取消；已启动的服务会保留，请查看状态后重试。");
            if (error is UnauthorizedAccessException || error is System.Security.SecurityException)
                return Loc.S("需要管理员权限；请右键以管理员身份运行后重试。");
            if (error is Win32Exception)
            {
                var win32 = (Win32Exception)error;
                if (win32.NativeErrorCode == 1223)
                    return Loc.S("已取消管理员授权，未做任何更改。");
                return Loc.S("无法启动所需程序；请确认安全软件未拦截，并以管理员身份重试。");
            }
            if (error is FileNotFoundException || error is DirectoryNotFoundException)
                return Loc.S("缺少必要文件，工具包可能不完整；请重新解压完整便携包后重试。");
            if (error is IOException)
                return Loc.S("文件或目录被占用，可能游戏或另一个启动器仍在运行；请关闭后重试。");
            if (error is WebException)
                return Loc.S("网络连接失败；请检查网络后重试。采集与存档不受影响。");
            if (error is TimeoutException)
                return Loc.S("操作超时；服务可能仍在启动。请稍后查看状态再重试。");
            if (error is FormatException || error is ArgumentException || error is InvalidCastException
                || error is NotSupportedException)
                return Loc.S("数据或路径格式不受支持；请确认选择的是正确的文件或目录后重试。");

            // Business checks raise InvalidOperationException with a finished
            // sentence already, so translate known backend lines and clean tracebacks.
            if (error is InvalidOperationException && !String.IsNullOrWhiteSpace(error.Message))
                return CleanBackendMessage(error.Message);

            return Loc.S("发生未预期错误，详细原因已写入运行记录；可点「打开日志文件夹」查看。");
        }

        internal static string CleanBackendMessage(string raw)
        {
            string summary = ExtractExceptionSummary(raw);
            return Loc.English ? summary : LocalizeBackendMessage(summary);
        }

        static string ExtractExceptionSummary(string raw)
        {
            if (String.IsNullOrWhiteSpace(raw)) return "";
            string normalized = raw.Replace("\r\n", "\n").Trim();
            if (normalized.IndexOf("Traceback (most recent call last):", StringComparison.Ordinal) >= 0)
            {
                string[] lines = normalized.Split(new[] { '\n' }, StringSplitOptions.RemoveEmptyEntries);
                for (int i = lines.Length - 1; i >= 0; i--)
                {
                    string candidate = lines[i].Trim();
                    if (candidate.Length == 0 || candidate.StartsWith("File \"") || candidate.StartsWith("^")) continue;
                    return candidate;
                }
            }
            return normalized;
        }

        static string LocalizeBackendMessage(string message)
        {
            if (String.IsNullOrWhiteSpace(message)) return "";
            if (message.IndexOf("No module named '", StringComparison.OrdinalIgnoreCase) >= 0)
                return "运行组件导入失败（缺少内部模块），请确认程序目录 app\\ 文件完整后重试。";
            if (message.IndexOf("LoloServer/Lolo.exe is missing", StringComparison.OrdinalIgnoreCase) >= 0)
                return "缺少服务端 LoloServer\\Lolo.exe：请选择包含 LoloServer 的目录，或把服务端放到启动器旁的 server-kit 文件夹。";
            if (message.IndexOf("private LAN IPv4 address is required", StringComparison.OrdinalIgnoreCase) >= 0)
                return "局域网服务需要本机私网 IPv4 地址（如 192.168.x.x），请检查「服务地址」设置。";
            if (message.IndexOf("Unsupported or unverified game channel", StringComparison.OrdinalIgnoreCase) >= 0)
                return "当前游戏客户端渠道尚未适配，请使用已验证的 PCGameSDK 或 QuickSDK 客户端版本。";
            if (message.IndexOf("Game client not found", StringComparison.OrdinalIgnoreCase) >= 0)
                return "未找到游戏客户端（launcher.exe / OverField Game），请点击「更改目录」重新选择客户端文件夹。";
            if (message.IndexOf("Selected game client is incomplete", StringComparison.OrdinalIgnoreCase) >= 0)
                return "所选游戏客户端文件不完整（缺少核心文件），请检查游戏目录或重新校验游戏文件。";
            if (message.IndexOf("symlink or Junction", StringComparison.OrdinalIgnoreCase) >= 0
                || message.IndexOf("linked client", StringComparison.OrdinalIgnoreCase) >= 0
                || message.IndexOf("reparse-point", StringComparison.OrdinalIgnoreCase) >= 0)
                return "目录安全性校验未通过（包含软链接或异常目录），请选择真实的本地游戏安装目录。";
            if (message.IndexOf("Profile operation failed", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                if (message.IndexOf("ClientVersion.json", StringComparison.OrdinalIgnoreCase) >= 0)
                    return "配置客户端版本映射失败：游戏文件可能不完整或 ClientVersion.json 异常。";
                return "准备运行配置失败（请核对游戏目录、服务端文件或局域网地址）；详细原因见运行记录。";
            }
            if (message.IndexOf("Profile service exited", StringComparison.OrdinalIgnoreCase) >= 0)
                return "本地服务端或网关启动后意外退出，请点击「打开日志文件夹」查看 server/rooms 错误日志。";
            if (message.IndexOf("Could not start this profile service", StringComparison.OrdinalIgnoreCase) >= 0)
                return "无法启动本地服务进程；请确认安全软件未拦截且 server-kit / python 文件完整。";
            if (message.IndexOf("occupied by another service", StringComparison.OrdinalIgnoreCase) >= 0
                || message.IndexOf("gateway port is occupied", StringComparison.OrdinalIgnoreCase) >= 0
                || message.IndexOf("address in use", StringComparison.OrdinalIgnoreCase) >= 0)
                return "所需本地端口已被占用，请先点击「停止服务」或关闭冲突程序后重试。";
            if (message.IndexOf("did not become ready or is owned by another process", StringComparison.OrdinalIgnoreCase) >= 0)
                return "本地服务端口未在规定时间内就绪或已被其他进程占用，请先停止服务或检查端口占用后重试。";
            if (message.IndexOf("Local UID client could not launch", StringComparison.OrdinalIgnoreCase) >= 0)
                return "本地游戏客户端未能启动，请点击「打开日志文件夹」查看 client-stderr.log。";
            if (message.IndexOf("UID account could not be prepared", StringComparison.OrdinalIgnoreCase) >= 0)
                return "准备本地 UID 账号失败，请确认存档数据库未被占用后重试。";
            if (message.IndexOf("Close the game before changing", StringComparison.OrdinalIgnoreCase) >= 0)
                return "请先退出正在运行的游戏客户端，再切换模式或修改 UID。";
            if (message.IndexOf("Stop the current world before switching", StringComparison.OrdinalIgnoreCase) >= 0)
                return "请先点击「停止服务」，再切换运行模式或局域网地址。";
            if (message.IndexOf("Another world operation is running", StringComparison.OrdinalIgnoreCase) >= 0)
                return "上一次服务操作仍在执行中，请稍候再试。";
            if (message.IndexOf("Active legacy services detected", StringComparison.OrdinalIgnoreCase) >= 0)
                return "检测到旧版启动器留下的运行中服务，请先用原启动器停止服务后再试。";
            if (message.IndexOf("Missing Frida", StringComparison.OrdinalIgnoreCase) >= 0)
                return "当前渠道客户端需要 Frida 17.24.3 运行依赖。";
            if (message.IndexOf("Client requests are not allowed in an account ZIP", StringComparison.OrdinalIgnoreCase) >= 0)
                return "账号 ZIP 包含客户端请求记录（c2s），安全策略已拦截保存与导入；请使用当前版本重新结束采集或重新解包。";
            if (message.IndexOf("Raw network captures cannot be imported or delivered", StringComparison.OrdinalIgnoreCase) >= 0)
                return "原始网络抓包文件（.etl / .pcapng）不能直接导入或保存为账号 ZIP。";
            if (message.IndexOf("Account ZIP has no verifiable UID", StringComparison.OrdinalIgnoreCase) >= 0)
                return "生成的账号 ZIP 中缺少有效 UID，请确认已登录公服并进入游戏场景。";
            if (message.IndexOf("Account ZIP contains sensitive credentials", StringComparison.OrdinalIgnoreCase) >= 0)
                return "检测到账号包内含敏感凭据字段，安全校验已拦截保存与导入。";
            if (message.IndexOf("Prepare this profile before importing", StringComparison.OrdinalIgnoreCase) >= 0)
                return "当前模式数据目录尚未初始化，请先准备配置（或启动一次服务）后再导入。";
            if (message.IndexOf("Capture contains conflicting", StringComparison.OrdinalIgnoreCase) >= 0)
                return "抓包角色或物品字段存在冲突，未能写入存档；请查看运行记录中的详细字段。";
            if (message.IndexOf("HTTP Error 403", StringComparison.OrdinalIgnoreCase) >= 0
                || message.IndexOf("HTTP Error 429", StringComparison.OrdinalIgnoreCase) >= 0
                || message.IndexOf("rate limit", StringComparison.OrdinalIgnoreCase) >= 0)
                return "更新检查访问接口受限，请稍后重试；原版本、采集与存档不受影响。";
            if (message.IndexOf("HTTP Error", StringComparison.OrdinalIgnoreCase) >= 0
                || message.IndexOf("URLError", StringComparison.OrdinalIgnoreCase) >= 0
                || message.IndexOf("timed out", StringComparison.OrdinalIgnoreCase) >= 0)
                return "更新服务器连接失败，请检查网络或稍后重试；原版本可继续使用。";
            return message;
        }

        /// <summary>Friendly sentence plus the action that failed, for status labels.</summary>
        public static string For(string action, Exception error)
        {
            string cause = Describe(error, action);
            if (String.IsNullOrEmpty(action)) return cause;
            return Loc.S("无法完成") + action + "：" + cause;
        }

        /// <summary>First line of a technical message, bounded, for the log panel.</summary>
        public static string Detail(Exception error)
        {
            if (error == null) return "";
            var text = new StringBuilder();
            for (Exception current = error; current != null; current = current.InnerException)
            {
                string line = (current.Message ?? "").Replace("\r", " ").Replace("\n", " ").Trim();
                if (line.Length == 0) continue;
                if (text.Length > 0) text.Append(" ← ");
                if (line.Length > 300) line = line.Substring(0, 300) + "…";
                text.Append(current.GetType().Name).Append(": ").Append(line);
            }
            return text.ToString();
        }

        /// <summary>Operation failures suggest inspecting the profile logs cleanly.</summary>
        public static string WithOperationHint(string action, string friendly)
        {
            var text = new StringBuilder(friendly ?? "");
            text.AppendLine().AppendLine().Append(Loc.S("请核对配置并可点击右下角「打开日志文件夹」查看详细原因。"));
            return text.ToString();
        }

        /// <summary>Capture failures get concrete causes instead of a bare message.</summary>
        public static string WithCaptureCauses(string friendly)
        {
            var text = new StringBuilder(friendly);
            text.AppendLine().AppendLine().AppendLine(Loc.S("常见原因与处理建议："));
            text.AppendLine("· " + Loc.S("是否以管理员身份运行？采集抓包需要管理员权限。"));
            text.AppendLine("· " + Loc.S("是否选择了原版公服 EXE，而不是私服副本？"));
            text.AppendLine("· " + Loc.S("公服客户端是否已经登录并进入场景？"));
            text.Append("· " + Loc.S("是否开启了会拦截抓包的加速器、代理或安全软件？"));
            return text.ToString();
        }
    }
}
