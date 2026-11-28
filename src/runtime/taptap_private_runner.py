"""Experimental, version-locked isolated QuickSDK private-client runner.

Never attach to a running official client. Only spawn a source-fingerprinted copy
under profiles/<mode>/client. Fail closed if instrumentation cannot install.
"""
import argparse
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
import sys
import threading
import time
from pathlib import Path

_SELF_DIR = str(Path(__file__).resolve().parent)
if _SELF_DIR not in sys.path:
    sys.path.insert(0, _SELF_DIR)

STEAM_APP_ID = '3879660'
SUPPORTED_GAME_ASSEMBLY = '2db98364241c95864781f77877523b9d2f728b22a91935fa30cdfb33a8b7f4ea'

# The Steam build ships the same QuickSDK types the adapter hooks, so it can run
# against a local world too. Each supported build is pinned by its own hash; an
# unknown binary still fails closed instead of being instrumented blindly.
SUPPORTED_BUILDS = {
    SUPPORTED_GAME_ASSEMBLY: 'quicksdk-pc',
    '4787fc3abaf911890fd6bccae6d0f85904f7c959a5d14c55f5dc56b75fe82cab': 'steam',
}
QUICKSDK_CHANNELS = ('quicksdk-pc', 'steam-quicksdk')


def validated_config(client, host, port, uid):
    from client_identity import classify, is_reparse_point
    requested = Path(client)
    if is_reparse_point(requested):
        raise ValueError('Adapter refuses a linked private-client root.')
    client = requested.resolve()
    if 'profiles' not in client.parts or client.name != 'client':
        raise ValueError('Adapter must run an isolated profiles/<mode>/client copy.')
    if is_reparse_point(client):
        raise ValueError('Adapter refuses a linked private-client root.')
    channel = classify(client)
    if channel not in QUICKSDK_CHANNELS:
        raise ValueError('This adapter supports verified QuickSDK PC layout only.')
    address = ipaddress.IPv4Address(host)
    if not (address.is_loopback or any(address in ipaddress.IPv4Network(n)
                                     for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))):
        raise ValueError('Only loopback or private LAN server addresses are permitted.')
    if isinstance(uid, bool) or not isinstance(uid, int) or not 1 <= uid <= 0xffffffff:
        raise ValueError('Invalid local UID; an integer is required.')
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('Invalid local port; an integer is required.')
    assembly = client / 'GameAssembly.dll'
    if is_reparse_point(assembly):
        raise ValueError('Adapter refuses a linked GameAssembly.dll.')
    digest = hashlib.sha256(assembly.read_bytes()).hexdigest()
    if digest not in SUPPORTED_BUILDS:
        raise ValueError('Unverified GameAssembly version; refuse to apply this experimental adapter.')
    is_steam = SUPPORTED_BUILDS[digest] == 'steam' or channel == 'steam-quicksdk'
    return {
        'host': str(address), 'port': port, 'uid': uid,
        'channel': channel, 'isSteam': is_steam,
        # Only the pinned Steam build opts out of the developer error overlay.
        # File logging and ordinary game failure/disconnection dialogs remain.
        'quietErrorOverlay': SUPPORTED_BUILDS[digest] == 'steam',
    }


def _validated_cache(client):
    from client_identity import cache_matches, classify, read_ready_marker
    marker = client / 'client-ready.json'
    recorded = read_ready_marker(marker)
    source = Path(recorded['source'])
    if recorded['adapter'] not in QUICKSDK_CHANNELS or classify(source) not in QUICKSDK_CHANNELS:
        raise ValueError('Private client marker does not identify a verified QuickSDK source.')
    if not cache_matches(source, marker):
        raise ValueError('Private client cache is stale, incomplete or tampered; prepare it again.')
    return source


def _private_output(path, profile):
    from client_identity import is_reparse_point
    path = Path(path)
    profile = Path(profile).resolve()
    if not path.is_absolute():
        path = profile / path
    if profile not in path.resolve().parents:
        raise ValueError('Client runtime output must stay inside its private profile.')
    if is_reparse_point(path):
        raise ValueError('Refusing a linked client runtime output file.')
    return path


def agent_source(config):
    text=Path(__file__).with_name('taptap_private_agent.js').read_text(encoding='utf-8')
    return text.replace('__LOCAL_CONFIGURATION__',json.dumps(config),1)


@contextmanager
def _steam_environment(enabled):
    """Expose Steam AppId/GameId during Steam private spawn and restore afterward."""
    if not enabled:
        yield
        return
    previous = {key: os.environ.get(key) for key in ('SteamAppId', 'SteamGameId')}
    os.environ['SteamAppId'] = STEAM_APP_ID
    os.environ['SteamGameId'] = STEAM_APP_ID
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_with_config(client, config, log_path, result_path=None, pid_path=None):
    client=Path(client).resolve()
    profile=client.parent
    log=_private_output(log_path,profile)
    result_path=_private_output(result_path,profile) if result_path else None
    pid_path=_private_output(pid_path,profile) if pid_path else None
    _validated_cache(client)
    import frida
    from service_runner import ProcessCleanupError, process_record, terminate_process_tree
    executable=client/'launcher.exe'
    # Revalidate after Python imports and immediately before process creation.
    validated_config(client,config['host'],config['port'],config['uid'])
    _validated_cache(client)
    device=frida.get_local_device()
    ready=threading.Event(); done=threading.Event(); failed=[]
    log.parent.mkdir(parents=True,exist_ok=True)
    def message(payload,data):
        event=payload.get('payload',{}) if payload.get('type')=='send' else {'event':'adapter-error','message':payload.get('description','Agent error')}
        with _private_output(log,profile).open('a',encoding='utf-8') as out:out.write(json.dumps(event,ensure_ascii=False)+'\n')
        if event.get('event')=='adapter-ready': ready.set()
        if event.get('event')=='adapter-error':failed.append(event);done.set()
    player_log=_private_output(log.with_name('Player.log'),profile)
    with _steam_environment(bool(config.get('isSteam'))):
        pid=device.spawn([str(executable),'-logFile',str(player_log)],cwd=str(client))
    record=None
    failure=None
    try:
        # Publish path and creation time with the PID.  A PID-only file is never
        # sufficient authority to kill a process.
        record=process_record(pid,executable)
        if pid_path:
            from profile_manager import write_json
            write_json(pid_path,record)
        session=device.attach(pid)
        session.on('detached',lambda *args:done.set())
        script=session.create_script(agent_source(config))
        script.on('message',message);script.load();device.resume(pid)
        start=time.monotonic()
        while not ready.is_set() and not done.is_set() and time.monotonic()-start<40:done.wait(.1)
        if not ready.is_set() or failed:
            raise RuntimeError('QuickSDK adapter failed before ready; terminating private test client.')
        result=dict(record,adapter=config.get('channel') or 'quicksdk-local',ready=True)
        if result_path:
            from profile_manager import write_json
            write_json(result_path,result)
        print(json.dumps(result),flush=True)
        done.wait()
        if failed:raise RuntimeError('Private adapter reported an error; terminating client.')
    except BaseException as error:
        failure=error
        raise
    finally:
        cleanup_errors=[]
        if record:
            try:terminate_process_tree(record)
            except Exception as error:cleanup_errors.append(str(error))
        else:
            cleanup_errors.append(f'Spawned PID {pid} could not be verified; it was not killed by PID.')
        if pid_path:
            from client_identity import is_reparse_point
            if not is_reparse_point(pid_path):pid_path.unlink(missing_ok=True)
        if failure is not None and result_path:
            # The parent preserves this structured record as quicksdk-failure-*.json.
            try:
                from profile_manager import write_json
                write_json(result_path,{'ready':False,'pid':pid,'process':record,'error':str(failure)})
            except Exception as error:cleanup_errors.append(f'failure record: {error}')
        if cleanup_errors:
            event={'event':'adapter-cleanup-failed','message':'; '.join(cleanup_errors),'pid':pid,'process':record}
            try:
                with _private_output(log,profile).open('a',encoding='utf-8') as out:out.write(json.dumps(event,ensure_ascii=False)+'\n')
            except Exception:pass
            error=ProcessCleanupError('QuickSDK cleanup failed: '+'; '.join(cleanup_errors),[record] if record else [])
            if failure is not None:raise error from failure
            raise error


def run(client,host,port,uid,log_path,result_path=None,pid_path=None):
    config=validated_config(client,host,port,uid)
    return run_with_config(client,config,log_path,result_path,pid_path)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--client',type=Path,required=True)
    p.add_argument('--host',default='127.0.0.1')
    p.add_argument('--port',type=int,default=18080)
    p.add_argument('--uid',type=int,default=1000000)
    p.add_argument('--log',type=Path,required=True)
    p.add_argument('--result-path',type=Path)
    p.add_argument('--pid-path',type=Path)
    a=p.parse_args();run(a.client,a.host,a.port,a.uid,a.log,a.result_path,a.pid_path)
