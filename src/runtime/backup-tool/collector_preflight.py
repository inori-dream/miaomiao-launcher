"""Read-only collector checks: no installs, downloads, capture or cleanup.

inspect_runtime(root, python, tshark, timeout=10) returns ready and checks.
States: ok/warning/error/unknown. Unknown required checks never count as ready.
CLI stdout is one JSON object; exit 0 means ready, 1 means not ready.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess


def probe(command, timeout=10):
    """Bound each native dependency probe and retain diagnostic output."""
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout,
                                encoding="utf-8", errors="replace",
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "unknown", str(exc)
    detail = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode != 0:
        return "error", f"Exit code {result.returncode}: {detail}"
    return "ok", detail or "Probe succeeded"


def inspect_runtime(collector_root, python, tshark, timeout=10):
    """Probe at most three native commands, each bounded by timeout seconds.

    Readiness covers required dependencies and elevation, not session ownership
    or output writability. Those remain the capture controller's responsibility.
    No game/process detection, registry changes or filesystem writes occur here.
    """
    if not 0 < timeout <= 30:
        raise ValueError("timeout must be in (0, 30] seconds")
    root = Path(collector_root).resolve()
    checks = []
    required = []

    def add(identifier, state, message, message_en, mandatory=True):
        checks.append({"id": identifier, "state": state, "message": message,
                       "messageEn": message_en})
        if mandatory:
            required.append(state)

    add("collector_root", "ok" if root.is_dir() else "error",
        f"工具目录：{root}", f"Collector directory: {root}")
    for filename in ("capture.ps1", "parse_pcap.py", "extract_gardens.py", "pcap_compat.py"):
        path = root / filename
        present = path.is_file()
        add("file:" + filename, "ok" if present else "error",
            f"{'已找到' if present else '缺少文件，请完整解压'}：{path}",
            f"{'Found' if present else 'Missing file; extract the complete package'}: {path}")
    for identifier, executable, arguments in (
        ("python", python, ["-B", "-X", "utf8", "-c", "import sys, cramjam, google.protobuf; print(sys.version)"]),
        ("tshark", tshark, ["--version"]),
    ):
        if not Path(executable).is_file():
            add(identifier, "error", f"找不到 {executable}；请完整解压或选择已有依赖，不会自动安装。",
                f"Missing {executable}; extract the complete package or select an existing dependency. Nothing will be installed automatically.")
        else:
            state, detail = probe([str(executable), *arguments], timeout)
            add(identifier, state, f"依赖探测 {executable}: {detail}",
                f"Dependency probe {executable}: {detail}")
    if os.name != "nt":
        add("windows", "error", "采集仅支持带 PktMon 的 Windows。", "Capture requires Windows with PktMon.")
        add("administrator", "unknown", "无法在非 Windows 上判断管理员权限。", "Cannot determine Windows elevation on this platform.")
        add("pktmon", "unknown", "无法在非 Windows 上探测 PktMon。", "Cannot probe PktMon on this platform.")
    else:
        add("windows", "ok", "Windows", "Windows")
        try:
            elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
            add("administrator", "ok" if elevated else "error",
                "已提升权限。" if elevated else "采集需要管理员权限；请以管理员身份启动。",
                "Running elevated." if elevated else "Capture requires elevation; run the launcher as administrator.")
        except (OSError, AttributeError) as exc:
            add("administrator", "unknown", f"权限探测失败：{exc}", f"Elevation probe failed: {exc}")
        pktmon = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "PktMon.exe"
        if pktmon.is_file():
            state, detail = probe([str(pktmon), "status"], timeout)
            add("pktmon", state, detail + "\n这里只查询状态，不停止或清理会话。",
                detail + "\nStatus query only; no sessions are stopped or cleaned up.")
        else:
            add("pktmon", "error", f"找不到 {pktmon}；请使用支持 PktMon 的 Windows。",
                f"Missing {pktmon}; use Windows with PktMon support.")
    suspicious = any(part.lower().startswith(("rar$", "7zo", "7ze", "temp1_")) for part in root.parts)
    add("location", "warning" if suspicious else "ok",
        "疑似压缩包预览临时目录；请全部解压到独立目录。" if suspicious else "请保留完整工具目录；备份含账号数据，ZIP 不要公开分享。",
        "Possible archive-preview temporary directory; fully extract to a dedicated folder." if suspicious else "Keep the complete tool directory. Backups contain account data; do not share ZIPs publicly.", False)
    add("output_permissions", "unknown", "只读预检查不创建文件；实际写入权限将在采集启动时验证。",
        "Read-only preflight does not create files; actual output writability is checked at capture startup.", False)
    return {"ready": bool(required) and all(state == "ok" for state in required), "checks": checks}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only collector preflight (JSON; no install, capture or cleanup)")
    parser.add_argument("--collector-root", type=Path, default=Path(__file__).parent)
    parser.add_argument("--python", type=Path)
    parser.add_argument("--tshark", type=Path)
    parser.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 30:
        parser.error("--timeout must be in (0, 30] seconds")
    root = args.collector_root
    python = args.python or Path(os.environ.get("OVERFIELD_BACKUP_PYTHON", str(root / "tools/python/python.exe")))
    tshark = args.tshark or Path(os.environ.get("OVERFIELD_TSHARK", str(root / "tools/tshark/tshark.exe")))
    result = inspect_runtime(root, python, tshark, args.timeout)
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
