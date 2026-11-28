"""Official-only executable validation and launch, independent of private gameRoot.
No local SDK injection, client preparation, dispatch redirection or UID arguments.

Two official delivery channels are accepted:
  * a plain game/launcher EXE (Bilibili PC build)
  * a Steam Internet Shortcut (.url) pointing at steam://rungameid/<appid>
Steam builds are started through the registered steam:// protocol handler because
the store client must attach the licence before the game starts.
"""
import ctypes
import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_SELF_DIR = str(Path(__file__).resolve().parent)
if _SELF_DIR not in sys.path:
    sys.path.insert(0, _SELF_DIR)
from urllib.parse import urlparse

STEAM_APP_ID = '3879660'
STEAM_GAME_URL = re.compile(r'^steam://rungameid/(\d+)/?$', re.IGNORECASE)
STEAM_PROTOCOL = 'steam://rungameid/{}'
SERVER_CONFIG = 'launcher_Data/StreamingAssets/Config.txt'
PRIVATE_ADDRESS = re.compile(r'^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.)')


def parse_vdf(text):
    """Parse Valve KeyValues (libraryfolders.vdf / appmanifest_*.acf) safely."""
    tokens = re.findall(r'"((?:\\.|[^"\\])*)"|([{}])', re.sub(r'(?m)^\s*//.*$', '', text))
    stack = [{}]
    key = None
    for quoted, brace in tokens:
        if brace == '{':
            if key is None:
                raise ValueError('Invalid VDF object.')
            child = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif brace == '}':
            if key is not None or len(stack) == 1:
                raise ValueError('Unbalanced VDF object.')
            stack.pop()
        else:
            value = re.sub(r'\\([\\"])', r'\1', quoted)
            if key is None:
                key = value
            else:
                stack[-1][key] = value
                key = None
    if len(stack) != 1 or key is not None:
        raise ValueError('Incomplete VDF file.')
    return stack[0]


class Launched:
    """Result of starting the configured official client."""
    def __init__(self, pid, kind, target):
        self.pid = pid
        self.kind = kind
        self.target = target

    def __repr__(self):
        return 'Launched(pid={0}, kind={1!r})'.format(self.pid, self.kind)


def _reject_private(path):
    """Shared guards: never point capture at a private copy or this launcher."""
    if path.name.lower() in ('overfieldlauncher.exe', 'lolo.exe'):
        raise ValueError('请选择原版公服启动器，不要选择私服启动器或服务端。')
    for parent in path.parents:
        if (parent / 'client-ready.json').is_file():
            raise ValueError('该目录是已准备的私服客户端副本，不能用于公服采集。')


def steam_app_id(value):
    """Return the Steam app id when the value is a Steam shortcut, else None."""
    path = Path(value)
    if path.suffix.lower() != '.url' or not path.is_file():
        return None
    text = path.read_text(encoding='utf-8-sig', errors='replace')
    for line in text.splitlines():
        if not line.strip().lower().startswith('url='):
            continue
        match = STEAM_GAME_URL.match(line.split('=', 1)[1].strip())
        if match:
            return match.group(1)
        raise ValueError('这个快捷方式不是 Steam 游戏链接，请选择 steam://rungameid/... 类型的快捷方式，'
                         '或直接选择公服客户端 EXE。')
    raise ValueError('这个快捷方式里没有 URL 字段，无法识别 Steam 游戏。')


def _steam_roots():
    """Steam install directories, read from the registry, never guessed online."""
    roots = []
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam'),
                          (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam')):
            try:
                with winreg.OpenKey(hive, key) as handle:
                    for name in ('SteamPath', 'InstallPath'):
                        try:
                            value = winreg.QueryValueEx(handle, name)[0]
                        except OSError:
                            continue
                        if value and Path(value).is_dir():
                            roots.append(Path(value))
            except OSError:
                continue
    except ImportError:
        pass
    return roots


def steam_install_dir(app_id):
    """Resolve the installed game folder for an app id, or None when unknown."""
    libraries = []
    seen = set()
    for root in _steam_roots():
        for folder_candidate in _library_folders_from_root(root):
            key = str(folder_candidate).lower()
            if key not in seen and folder_candidate.is_dir():
                seen.add(key)
                libraries.append(folder_candidate)
    for library in libraries:
        manifest = library / 'steamapps' / 'appmanifest_{0}.acf'.format(app_id)
        if not manifest.is_file():
            continue
        text = manifest.read_text(encoding='utf-8-sig', errors='replace')
        installdir = None
        try:
            installdir = parse_vdf(text).get('AppState', {}).get('installdir')
        except ValueError:
            installdir = None
        if not installdir:
            match = re.search(r'"installdir"\s+"([^"]+)"', text)
            installdir = match.group(1) if match else None
        if not installdir:
            continue
        folder = library / 'steamapps' / 'common' / installdir
        if folder.is_dir():
            return folder
    return None


def _library_folders_from_root(root):
    folders = [root]
    listing = root / 'steamapps' / 'libraryfolders.vdf'
    if not listing.is_file():
        return folders
    text = listing.read_text(encoding='utf-8-sig', errors='replace')
    parsed_any = False
    try:
        entries = parse_vdf(text).get('libraryfolders', {})
        if isinstance(entries, dict):
            for entry in entries.values():
                if isinstance(entry, dict) and isinstance(entry.get('path'), str):
                    folders.append(Path(entry['path']))
                    parsed_any = True
    except ValueError:
        parsed_any = False
    if not parsed_any:
        for match in re.finditer(r'"path"\s+"([^"]+)"', text):
            folders.append(Path(match.group(1).replace('\\\\', '\\')))
    return folders


def resolve_client_dir(path):
    """Resolve the game directory containing launcher_Data from an EXE or .url."""
    path = Path(path)
    app_id = steam_app_id(path)
    if app_id is not None:
        return steam_install_dir(app_id)
    folder = path if path.is_dir() else path.parent
    for candidate in (folder, folder / 'OverField Game'):
        if (candidate / 'launcher_Data').is_dir() or (candidate / 'launcher.exe').is_file():
            return candidate
    return folder


def detect_channel(value):
    """Identify official/private client channel ('bilibili', 'taptap', 'steam', or 'unknown')."""
    if not value:
        return {'channel': 'unknown', 'identity': 'unknown', 'clientDir': ''}
    path = Path(value)
    app_id = steam_app_id(path)
    if app_id is not None:
        install = steam_install_dir(app_id)
        return {
            'channel': 'steam',
            'identity': 'steam-quicksdk',
            'appId': app_id,
            'clientDir': str(install) if install else '',
        }
    client_dir = resolve_client_dir(path)
    if client_dir is None or not client_dir.is_dir():
        return {'channel': 'unknown', 'identity': 'unknown', 'clientDir': ''}
    try:
        from client_identity import classify
        identity = classify(client_dir)
    except (ValueError, OSError, ImportError):
        identity = 'unknown'
    if identity == 'unknown' and (client_dir / 'launcher_Data/Plugins/x86_64/steam_api64.dll').is_file():
        identity = 'steam-quicksdk'
    if identity == 'steam-quicksdk':
        channel = 'steam'
    elif identity == 'pcgamesdk':
        channel = 'bilibili'
    elif identity in ('quicksdk-pc', 'unverified-tap'):
        channel = 'taptap'
    else:
        channel = 'unknown'
    return {'channel': channel, 'identity': identity, 'clientDir': str(client_dir)}


def discover_clients():
    """Read-only auto-discovery of installed official clients (currently Steam library)."""
    found = {}
    steam = steam_install_dir(STEAM_APP_ID)
    if steam is not None and (steam / 'launcher.exe').is_file():
        exe = steam / 'launcher.exe'
        info = detect_channel(exe)
        found['steam'] = {'path': str(exe), 'dir': str(steam), 'appId': STEAM_APP_ID, **info}
    return found


def server_addresses(client):
    """Public server addresses the client itself is configured to connect to.

    Some channels (the Steam build) choose game ports at runtime, so capture has
    to key on the address instead. Only addresses the client publishes are used.
    """
    if not client:
        return []
    config = Path(client) / SERVER_CONFIG
    if not config.is_file():
        return []
    found = []
    try:
        text = config.read_text(encoding='utf-8-sig', errors='replace')
    except OSError:
        return []
    for name in ('RemoteServerIP', 'LocalServerIP'):
        for match in re.finditer(r'^\s*' + name + r'\s*=\s*([^\s#;]+)', text, re.MULTILINE):
            value = match.group(1).strip()
            if re.fullmatch(r'\d{1,3}(\.\d{1,3}){3}', value) and not PRIVATE_ADDRESS.match(value):
                if value not in found:
                    found.append(value)
    return found


def validate_launcher(value, base=None):
    if not value or not str(value).strip():
        raise ValueError('请先设置公服启动器路径；该路径与私服游戏目录独立。')
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise ValueError('请选择已存在的公服启动器、原版客户端 EXE，或 Steam 游戏快捷方式。')
    path = path.resolve()
    here = Path(__file__).resolve().parent
    parent = here.parent
    default_base = parent if (here.name.lower() == 'app' and any((
        (parent / 'OverFieldLauncher.exe').is_file(),
        (parent / 'backup-tool').is_dir(),
        (parent / 'server-kit').is_dir(),
        (parent / 'profiles').is_dir(),
        (parent / 'runtime').is_dir(),
    ))) else here
    base = Path(base or default_base).resolve()
    private = base / 'profiles'
    if path == private or private in path.parents:
        raise ValueError('公服路径不能指向启动器 profiles 内的私服副本。')
    app_id = steam_app_id(path)
    if app_id is not None:
        # Validate the installed Steam copy too when it can be located.
        install = steam_install_dir(app_id)
        if install is not None:
            _reject_private(install / 'launcher.exe')
            if private == install or private in install.parents:
                raise ValueError('这个 Steam 快捷方式指向启动器 profiles 内的私服副本。')
        return path
    if path.suffix.lower() != '.exe':
        raise ValueError('请选择公服启动器 EXE，或 Steam 游戏快捷方式（.url）。')
    _reject_private(path)
    sdk = path.parent / 'launcher_Data/Plugins/x86_64/PCGameSDK.dll'
    local = next((candidate for candidate in (
        base / 'app' / 'LocalUidSDK.dll',
        base / 'LocalUidSDK.dll',
        Path(__file__).with_name('LocalUidSDK.dll'),
    ) if candidate.is_file()), base / 'LocalUidSDK.dll')
    if sdk.is_file() and local.is_file() and sdk.stat().st_size == local.stat().st_size:
        if hashlib.sha256(sdk.read_bytes()).digest() == hashlib.sha256(local.read_bytes()).digest():
            raise ValueError('这个客户端已经替换本地UID SDK，请选择未修改的公服客户端。')
    return path


def clean_environment(environ=None):
    return {k: v for k, v in (os.environ if environ is None else environ).items()
            if not k.upper().startswith('LOLO_')}


def _process_paths():
    """Map running pid -> executable path using only the Windows toolhelp API."""
    if os.name != 'nt':
        return {}
    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', ctypes.c_ulong), ('cntUsage', ctypes.c_ulong),
                    ('th32ProcessID', ctypes.c_ulong), ('th32DefaultHeapID', ctypes.c_void_p),
                    ('th32ModuleID', ctypes.c_ulong), ('cntThreads', ctypes.c_ulong),
                    ('th32ParentProcessID', ctypes.c_ulong), ('pcPriClassBase', ctypes.c_long),
                    ('dwFlags', ctypes.c_ulong), ('szExeFile', ctypes.c_char * 260)]
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == -1:
        return {}
    found = {}
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        more = kernel32.Process32First(snapshot, ctypes.byref(entry))
        while more:
            handle = kernel32.OpenProcess(0x1000, False, entry.th32ProcessID)
            if handle:
                buffer = ctypes.create_unicode_buffer(1024)
                size = ctypes.c_ulong(1024)
                if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    found[entry.th32ProcessID] = buffer.value
                kernel32.CloseHandle(handle)
            more = kernel32.Process32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def _await_steam_game(install, timeout=45.0):
    """Steam starts the game asynchronously, so wait for its process to appear."""
    if install is None:
        return 0
    expected = str(install / 'launcher.exe').lower()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for pid, path in _process_paths().items():
            if path and path.lower() == expected:
                return pid
        time.sleep(0.4)
    return 0


def launch_official(value, base=None):
    """Start the configured official client visibly, without any private injection."""
    path = validate_launcher(value, base)
    app_id = steam_app_id(path)
    if app_id is not None:
        url = STEAM_PROTOCOL.format(app_id)
        install = steam_install_dir(app_id)
        # os.startfile hands the URL to the registered handler, which starts Steam
        # and the licensed game. No shell string and no private environment.
        os.startfile(url)
        return Launched(_await_steam_game(install), 'steam', install or url)
    env = clean_environment()
    channel_info = detect_channel(path)
    kind = 'exe'
    if channel_info.get('channel') == 'steam':
        env.setdefault('SteamAppId', STEAM_APP_ID)
        env.setdefault('SteamGameId', STEAM_APP_ID)
        kind = 'steam'
    return Launched(subprocess.Popen([str(path)], cwd=str(path.parent), env=env,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, creationflags=0).pid, kind, path.parent)
