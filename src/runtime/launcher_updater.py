"""Safe stable-release discovery/download. Never installs or launches payloads.
transport(url) may return bytes or a read/close/geturl response for offline tests.
stream accepts an event dict callback, or a text stream receiving JSONL.
"""
import argparse
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import stat
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile

_SELF_DIR = str(Path(__file__).resolve().parent)
if _SELF_DIR not in sys.path:
    sys.path.insert(0, _SELF_DIR)

from update_trust import (REPOSITORY, MAX_MANIFEST, MAX_PAYLOAD, TrustError,
                          asset_url, safe_relative, strict_json, verify_manifest, version_tuple)

LATEST_URL = 'https://api.github.com/repos/'+REPOSITORY+'/releases/latest'
WEB_LATEST_URL = 'https://github.com/'+REPOSITORY+'/releases/latest'
CACHE_SECONDS = 3600
TIMEOUT = 20
REDIRECT_HOSTS = {'github.com','api.github.com','release-assets.githubusercontent.com','objects.githubusercontent.com','github-releases.githubusercontent.com'}


def _allowed(url):
    from urllib.parse import urlsplit
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname not in REDIRECT_HOSTS or p.username or p.password or p.port not in (None,443):
        raise TrustError('untrusted network URL')


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _allowed(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _transport(url, method=None):
    _allowed(url)
    req_method = method or ('HEAD' if url == WEB_LATEST_URL else 'GET')
    headers = {'User-Agent': 'OverFieldLauncher-Updater/1',
               'Accept': 'application/vnd.github+json' if url == LATEST_URL else 'application/octet-stream'}
    opener = urllib.request.build_opener(_Redirect())
    for attempt in range(3):
        try:
            return opener.open(urllib.request.Request(url, method=req_method, headers=headers), timeout=TIMEOUT)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, OSError):
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))


def _guard(path):
    """Reject symlinks/junctions in every existing ancestor, including root."""
    path = Path(os.path.abspath(path))
    for part in [*reversed(path.parents), path]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise TrustError('reparse-point boundary: '+str(part))
    return path


def _mkdir(path):
    _guard(path)
    path.mkdir(parents=True, exist_ok=True)
    _guard(path)


def _rename(source, destination):
    for attempt in range(10):
        try:
            os.rename(source, destination)
            return
        except OSError as error:
            if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 9:
                raise
            time.sleep(0.1)


def _atomic(path, raw):
    _guard(path)
    temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        _guard(temp)
        with temp.open('xb') as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        _guard(path)
        _guard(temp)
        os.replace(temp, path)
    finally:
        _guard(temp)
        if temp.exists():
            temp.unlink()


def _stream_sha256(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(chunk)
    return digest.hexdigest()


def _event(stream, **event):
    if stream is None:
        return
    try:
        if callable(stream):
            stream(event)
        else:
            stream.write(json.dumps(event, ensure_ascii=False)+'\n')
            stream.flush()
    except Exception:
        pass  # Observers cannot break safe startup.


def _fetch(url, limit, transport, stream=None, target=None, expected_size=None, expected_hash=None):
    _allowed(url)
    response = transport(url)
    if isinstance(response, bytes):
        response = io.BytesIO(response)
    out = None
    try:
        if hasattr(response, 'geturl'):
            _allowed(response.geturl())
        if target is not None:
            _guard(target)
            out = target.open('xb')
        data = bytearray()
        digest = hashlib.sha256()
        size = 0
        start = time.monotonic()
        while True:
            chunk = response.read(min(65536, limit-size+1))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise TrustError('response exceeds size limit')
            digest.update(chunk)
            if out:
                out.write(chunk)
            else:
                data.extend(chunk)
            elapsed = time.monotonic()-start
            if elapsed > 120:
                raise TrustError('transfer total timeout')
            if target is not None:
                _event(stream, event='progress', downloaded=size, total=expected_size)
                if elapsed > 30 and size/elapsed < 1024*1024:
                    raise TrustError('download deferred: sustained speed below 1 MB/s')
            elif elapsed > 60:
                raise TrustError('metadata timeout')
        if expected_size is not None and size != expected_size:
            raise TrustError('payload size mismatch')
        if expected_hash is not None and digest.hexdigest() != expected_hash:
            raise TrustError('payload sha256 mismatch')
        if out:
            out.flush()
            os.fsync(out.fileno())
        return bytes(data)
    finally:
        if out:
            out.close()
        response.close()


def _zip_validate(path, asset):
    _guard(path)
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) != len(asset['files']):
            raise TrustError('ZIP inventory mismatch')
        seen = set()
        for info in infos:
            safe_relative(info.filename)
            if info.filename in seen or info.filename not in asset['files'] or info.is_dir() or info.flag_bits & 1:
                raise TrustError('invalid ZIP entry')
            seen.add(info.filename)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise TrustError('ZIP symlink')
            expected = asset['files'][info.filename]
            if info.file_size != expected['size']:
                raise TrustError('ZIP entry size mismatch')
            digest = hashlib.sha256()
            size = 0
            with archive.open(info) as source:
                while True:
                    chunk = source.read(65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > expected['size']:
                        raise TrustError('ZIP expansion limit')
                    digest.update(chunk)
            if digest.hexdigest() != expected['sha256']:
                raise TrustError('ZIP entry hash mismatch')


def _discover_web_tag(fetch):
    """Resolve the latest published release tag from GitHub's web redirect without REST API quota."""
    _allowed(WEB_LATEST_URL)
    response = fetch(WEB_LATEST_URL)
    if isinstance(response, bytes):
        raise TrustError('missing web release redirect')
    try:
        final_url = response.geturl() if hasattr(response, 'geturl') else ''
        _allowed(final_url)
    finally:
        response.close()
    prefix = 'https://github.com/' + REPOSITORY + '/releases/tag/'
    if not final_url.startswith(prefix):
        raise TrustError('invalid release redirect')
    tag = final_url[len(prefix):].split('?', 1)[0].split('#', 1)[0]
    if not tag.startswith('v') or '/' in tag:
        raise TrustError('invalid release tag')
    version_tuple(tag[1:])
    return tag


def _discover_release(fetch):
    """Query the GitHub Releases API, falling back to /releases/latest redirect on 403/429 or transient API network errors."""
    try:
        release = strict_json(_fetch(LATEST_URL, MAX_MANIFEST, fetch))
    except urllib.error.HTTPError as error:
        if error.code not in (403, 429):
            raise
        tag = _discover_web_tag(fetch)
        selected = {name: asset_url(tag, name) for name in ('launcher-update.json', 'launcher-update.sig')}
        return tag, None, selected
    except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, OSError):
        tag = _discover_web_tag(fetch)
        selected = {name: asset_url(tag, name) for name in ('launcher-update.json', 'launcher-update.sig')}
        return tag, None, selected
    if not isinstance(release, dict) or release.get('draft') is not False or release.get('prerelease') is not False:
        raise TrustError('not a formal stable release')
    tag = release.get('tag_name')
    if not isinstance(tag, str) or not tag.startswith('v'):
        raise TrustError('invalid release tag')
    version_tuple(tag[1:])
    assets = release.get('assets')
    if not isinstance(assets, list):
        raise TrustError('missing release assets')
    selected = {}
    for name in ('launcher-update.json','launcher-update.sig'):
        matches = [a for a in assets if isinstance(a,dict) and a.get('name') == name]
        if len(matches) != 1 or matches[0].get('browser_download_url') != asset_url(tag,name):
            raise TrustError('missing or ambiguous signed release metadata')
        selected[name] = matches[0]['browser_download_url']
    return tag, assets, selected


def check_update(root, edition, current_version, force=False, transport=None, stream=None):
    """Return status, version, stage_dir, optional message; errors never escape.
    Cache is only a throttle, never an authority for trusting staged updates.
    """
    result = {'status':'error', 'version':None, 'stage_dir':None}
    temp = None
    state = None
    try:
        if edition not in ('UNIFIED','CN','EN'):
            raise TrustError('edition must be UNIFIED, CN or EN')
        current = version_tuple(current_version)
        root = _guard(root)
        updates = root/'runtime'/'updates'
        _mkdir(updates)
        state = updates/'update-state.json'
        _guard(state)
        if not force and state.exists():
            if state.stat().st_size > MAX_MANIFEST:
                raise TrustError('oversized state cache')
            cached = strict_json(state.read_bytes())
            now = time.time()
            stamp = cached.get('checked_at', 0)
            if isinstance(stamp, (int,float)) and 0 <= now-stamp < CACHE_SECONDS and cached.get('edition') == edition and cached.get('current_version') == current_version and cached.get('status') in ('downloaded', 'up_to_date', 'deferred'):
                if cached.get('status') == 'downloaded':
                    # Restore a pending download only from reverified signed bytes,
                    # never from a cache-provided filesystem path.
                    version_tuple(cached.get('version'))
                    stage = updates/'staging'/(cached['version']+'-'+edition)
                    _guard(stage)
                    metadata = _guard(stage/'manifest.json')
                    signature = _guard(stage/'manifest.sig')
                    payload = _guard(stage/'payload.zip')
                    if not 0 < metadata.stat().st_size <= MAX_MANIFEST or signature.stat().st_size != 256:
                        raise TrustError('invalid cached staging metadata size')
                    manifest = verify_manifest(metadata.read_bytes(), signature.read_bytes())
                    if manifest['version'] != cached['version'] or version_tuple(manifest['version']) <= current:
                        raise TrustError('cached staging version mismatch')
                    asset = manifest['assets'][edition]
                    if payload.stat().st_size != asset['size']:
                        raise TrustError('cached payload size mismatch')
                    with payload.open('rb') as source:
                        if _stream_sha256(source) != asset['sha256']:
                            raise TrustError('cached payload hash mismatch')
                    _zip_validate(payload, asset)
                    restored = dict(result, status='downloaded', version=manifest['version'], stage_dir=str(stage))
                    _event(stream, event='result', **restored)
                    return restored
                deferred = dict(result, status='deferred', version=cached.get('version'), message='cached check; use --force to refresh')
                _event(stream, event='result', **deferred)
                return deferred
        fetch = transport or _transport
        _event(stream, event='checking', edition=edition)
        tag, assets, selected = _discover_release(fetch)
        raw = _fetch(selected['launcher-update.json'], MAX_MANIFEST, fetch)
        sig = _fetch(selected['launcher-update.sig'], 256, fetch)
        manifest = verify_manifest(raw, sig)
        if manifest['releaseTag'] != tag:
            raise TrustError('release version mismatch')
        result['version'] = manifest['version']
        if version_tuple(manifest['version']) <= current:
            if version_tuple(manifest['version']) < current:
                raise TrustError('downgrade rejected')
            result['status'] = 'up_to_date'
        else:
            asset = manifest['assets'][edition]
            if assets is not None:
                payload_matches = [a for a in assets if isinstance(a, dict) and a.get('name') == asset['name']]
                if len(payload_matches) != 1 or payload_matches[0].get('browser_download_url') != asset['url']:
                    raise TrustError('signed package absent from release')
            staging = updates/'staging'
            _mkdir(staging)
            stage = staging/(manifest['version']+'-'+edition)
            _guard(stage)
            # Existing complete staging is reverified; never overwrite a published stage.
            if stage.exists():
                for name in ('manifest.json','manifest.sig','payload.zip'):
                    _guard(stage/name)
                if (stage/'manifest.json').stat().st_size != len(raw) or (stage/'manifest.sig').stat().st_size != 256:
                    raise TrustError('staged metadata size mismatch')
                if (stage/'manifest.json').read_bytes() != raw or (stage/'manifest.sig').read_bytes() != sig:
                    raise TrustError('staging conflict')
                p = stage/'payload.zip'
                if p.stat().st_size != asset['size']:
                    raise TrustError('staged payload size mismatch')
                with p.open('rb') as source:
                    digest = _stream_sha256(source)
                if digest != asset['sha256']:
                    raise TrustError('staged payload hash mismatch')
                _zip_validate(p, asset)
            else:
                temp = staging/('.pending-'+uuid.uuid4().hex)
                _mkdir(temp)
                _fetch(asset['url'], min(MAX_PAYLOAD,asset['size']), fetch, stream, temp/'payload.zip', asset['size'], asset['sha256'])
                _zip_validate(temp/'payload.zip', asset)
                _atomic(temp/'manifest.json', raw)
                _atomic(temp/'manifest.sig', sig)
                _guard(stage)
                _guard(temp)
                _rename(temp, stage)
                temp = None
            result.update(status='downloaded', stage_dir=str(stage))
        _atomic(state, json.dumps(dict(result, checked_at=time.time(), edition=edition, current_version=current_version)).encode())
    except Exception as exc:
        result.update(status='deferred' if 'speed below' in str(exc) else 'error', message=str(exc), stage_dir=None)
        if state is not None:
            try:
                _atomic(state, json.dumps(dict(result, checked_at=time.time(), edition=edition, current_version=current_version)).encode())
            except Exception:
                pass
    finally:
        if temp is not None:
            try:
                _guard(temp)
                for name in ('payload.zip','manifest.json','manifest.sig'):
                    child = _guard(temp/name)
                    if child.exists():
                        child.unlink()
                temp.rmdir()
            except Exception:
                pass
    _event(stream, event='result', **result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    check = sub.add_parser('check')
    check.add_argument('--root', required=True)
    check.add_argument('--edition', choices=('UNIFIED','CN','EN'), required=True)
    check.add_argument('--current-version', required=True)
    check.add_argument('--force', action='store_true')
    check.add_argument('--events', action='store_true')
    check.add_argument('--json', action='store_true', help='JSON is also the default output')
    args = parser.parse_args(argv)
    import sys
    result = check_update(args.root,args.edition,args.current_version,args.force,stream=sys.stdout if args.events else None)
    if not args.events:
        print(json.dumps(result,ensure_ascii=False))
    return 0 if result['status'] != 'error' else 1

if __name__ == '__main__':
    raise SystemExit(main())
