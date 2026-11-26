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
            // sentence already, so relay it as-is instead of replacing it.
            if (error is InvalidOperationException && !String.IsNullOrWhiteSpace(error.Message))
                return error.Message;

            return Loc.S("发生未预期错误，详细原因已写入运行记录；可点「打开日志文件夹」查看。");
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

        /// <summary>Capture failures get three concrete causes instead of a bare message.</summary>
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
