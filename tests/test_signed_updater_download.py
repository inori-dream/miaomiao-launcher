"""Offline synthetic RSA/ZIP fixtures; never reads production private keys/network."""
import hashlib
import io
import json
import math
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import launcher_updater as updater
import update_trust as trust


def synthetic_key():
    rng = random.Random(712967)
    def prime():
        while True:
            n = rng.getrandbits(1024) | (1 << 1023) | 1
            if any(n % p == 0 for p in (3,5,7,11,13,17,19,23,29,31,37)):
                continue
            d, s = n-1, 0
            while d % 2 == 0:
                d //= 2
                s += 1
            for a in (2,3,5,7,11,13,17,19,23,29,31,37):
                x = pow(a,d,n)
                if x in (1,n-1):
                    continue
                for _ in range(s-1):
                    x = pow(x,2,n)
                    if x == n-1:
                        break
                else:
                    break
            else:
                return n
    p, q = prime(), prime()
    phi = (p-1)*(q-1)
    return p*q, pow(65537,-1,phi)


class SignedDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n, cls.d = synthetic_key()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.pin = patch.object(trust,'N_HEX',format(self.n,'x'))
        self.pin.start()
        self.content = b'print("synthetic core")\n'
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('launcher.py',self.content)
        self.payload = buffer.getvalue()
        self.manifest = {'schemaVersion':1,'repository':trust.REPOSITORY,'version':'1.4.0','releaseTag':'v1.4.0','channel':'stable','minUpdaterVersion':1,'assets':{}}
        for edition in ('CN','EN'):
            name = 'OverFieldLauncher-'+edition+'-update.zip'
            self.manifest['assets'][edition] = {'name':name,'url':trust.asset_url('v1.4.0',name),'size':len(self.payload),'sha256':hashlib.sha256(self.payload).hexdigest(),'files':{'launcher.py':{'size':len(self.content),'sha256':hashlib.sha256(self.content).hexdigest()}}}
        self.calls = []

    def tearDown(self):
        self.pin.stop()
        self.directory.cleanup()

    def sign(self,raw):
        info = bytes.fromhex('3031300d060960864801650304020105000420')+hashlib.sha256(raw).digest()
        em = b'\x00\x01'+b'\xff'*(256-len(info)-3)+b'\x00'+info
        return pow(int.from_bytes(em,'big'),self.d,self.n).to_bytes(256,'big')

    def transport(self, url):
        self.calls.append(url)
        raw = json.dumps(self.manifest).encode()
        if url == updater.LATEST_URL:
            names = ['launcher-update.json','launcher-update.sig']+[x['name'] for x in self.manifest['assets'].values()]
            return json.dumps({'draft':False,'prerelease':False,'tag_name':'v1.4.0','assets':[{'name':n,'browser_download_url':trust.asset_url('v1.4.0',n)} for n in names]}).encode()
        if url.endswith('launcher-update.json'):
            return raw
        if url.endswith('launcher-update.sig'):
            return self.sign(raw)
        return self.payload

    def check(self, **kw):
        return updater.check_update(self.root,'CN',kw.pop('current_version','1.3.0'),transport=self.transport,**kw)

    def test_success_atomic_stage_cache_and_force(self):
        events = []
        result = self.check(stream=events.append)
        self.assertEqual(result['status'],'downloaded',result)
        stage = Path(result['stage_dir'])
        self.assertEqual(set(p.name for p in stage.iterdir()),{'manifest.json','manifest.sig','payload.zip'})
        self.assertEqual(trust.verify_manifest((stage/'manifest.json').read_bytes(),(stage/'manifest.sig').read_bytes())['version'],'1.4.0')
        self.assertTrue(any(e['event']=='progress' for e in events))
        self.calls.clear()
        self.assertEqual(self.check()['status'],'downloaded')
        self.assertEqual(self.calls,[])
        self.assertEqual(self.check(force=True)['status'],'downloaded')

    def test_signature_tamper_length_and_json_duplicate(self):
        raw = json.dumps(self.manifest).encode()
        trust.verify_manifest(raw,self.sign(raw))
        for bad_raw, sig in [(raw+b' ',self.sign(raw)),(raw,b'0'*255),(b'{"x":1,"x":2}',self.sign(b'{"x":1,"x":2}'))]:
            with self.assertRaises(trust.TrustError):
                trust.verify_manifest(bad_raw,sig)

    def test_reject_wrong_origin_edition_and_paths(self):
        for mutate in [lambda m:m.update(repository='evil/repo'),lambda m:m['assets']['CN'].update(url='http://github.com/evil'),lambda m:m['assets']['CN'].update(name='OverFieldLauncher-EN-update.zip'),lambda m:m['assets']['CN'].update(files={'../evil':{'size':1,'sha256':'0'*64}}),lambda m:m.update(minUpdaterVersion=True)]:
            original = json.loads(json.dumps(self.manifest))
            mutate(self.manifest)
            raw = json.dumps(self.manifest).encode()
            with self.assertRaises(trust.TrustError):
                trust.verify_manifest(raw,self.sign(raw))
            self.manifest = original

    def test_corrupt_payload_no_published_stage(self):
        self.payload += b'corrupt'
        result = self.check()
        self.assertEqual(result['status'],'error')
        self.assertFalse((self.root/'runtime/updates/staging/1.4.0-CN').exists())
        self.assertEqual(list((self.root/'runtime/updates/staging').iterdir()),[])

    def test_equal_and_downgrade(self):
        self.assertEqual(self.check(current_version='1.4.0')['status'],'up_to_date')
        self.assertEqual(self.check(current_version='1.5.0',force=True)['status'],'error')

    def test_network_failure_contained(self):
        def fail(url):
            raise TimeoutError('offline')
        self.assertEqual(updater.check_update(self.root,'EN','1.3.0',transport=fail)['status'],'error')

    def test_disallowed_redirect(self):
        for url in ('http://github.com/x','https://github.com.evil/x','https://evil.com/x','https://user@github.com/x'):
            with self.assertRaises(trust.TrustError):
                updater._allowed(url)

    def test_windows_junction_attribute_rejected(self):
        original = Path.lstat
        class JunctionStat:
            st_mode = 0o40755
            st_file_attributes = 0x400
        def synthetic_lstat(path):
            if path.name == 'runtime':
                return JunctionStat()
            return original(path)
        with patch.object(Path, 'lstat', synthetic_lstat):
            self.assertEqual(self.check()['status'],'error')
        self.assertFalse((self.root/'runtime').exists())

    def test_api_rate_limit_falls_back_to_web_redirect(self):
        import urllib.error
        class RedirectResponse(io.BytesIO):
            def __init__(self, final_url):
                super().__init__(b'')
                self._final_url = final_url
            def geturl(self):
                return self._final_url
        def rate_limited(url):
            self.calls.append(url)
            if url == updater.LATEST_URL:
                raise urllib.error.HTTPError(url, 403, 'rate limit exceeded', {}, None)
            if url == updater.WEB_LATEST_URL:
                return RedirectResponse('https://github.com/' + trust.REPOSITORY + '/releases/tag/v1.4.0')
            return self.transport(url)
        result = updater.check_update(self.root, 'EN', '1.3.0', transport=rate_limited)
        self.assertEqual(result['status'], 'downloaded', result)
        self.assertIn(updater.WEB_LATEST_URL, self.calls)
        def bad_redirect(url):
            if url == updater.LATEST_URL:
                raise urllib.error.HTTPError(url, 429, 'too many requests', {}, None)
            if url == updater.WEB_LATEST_URL:
                return RedirectResponse('https://github.com/' + trust.REPOSITORY + '/releases')
            return self.transport(url)
        self.assertEqual(updater.check_update(self.root, 'EN', '1.3.0', force=True, transport=bad_redirect)['status'], 'error')

    def test_api_network_failure_falls_back_to_web_redirect_and_errors_not_cached(self):
        import urllib.error
        class RedirectResponse(io.BytesIO):
            def __init__(self, final_url):
                super().__init__(b'')
                self._final_url = final_url
            def geturl(self):
                return self._final_url
        def transient_api_down(url):
            self.calls.append(url)
            if url == updater.LATEST_URL:
                raise urllib.error.URLError('connection reset')
            if url == updater.WEB_LATEST_URL:
                return RedirectResponse('https://github.com/' + trust.REPOSITORY + '/releases/tag/v1.4.0')
            return self.transport(url)
        result = updater.check_update(self.root, 'CN', '1.3.0', transport=transient_api_down)
        self.assertEqual(result['status'], 'downloaded', result)
        state = json.loads((self.root / 'runtime/updates/update-state.json').read_text(encoding='utf-8'))
        state.update(status='error', message='network glitch')
        (self.root / 'runtime/updates/update-state.json').write_text(json.dumps(state), encoding='utf-8')
        self.calls.clear()
        retried = updater.check_update(self.root, 'CN', '1.3.0', transport=self.transport)
        self.assertEqual(retried['status'], 'downloaded', retried)
        self.assertIn(updater.LATEST_URL, self.calls)

    def test_transport_retries_transient_urlerror_only(self):
        import urllib.error
        attempts = []
        class FakeOpener:
            def open(self, req, timeout=None):
                attempts.append(timeout)
                if len(attempts) < 3:
                    raise urllib.error.URLError('temporary timeout')
                return io.BytesIO(b'ok')
        with patch.object(updater.urllib.request, 'build_opener', return_value=FakeOpener()), \
                patch.object(updater.time, 'sleep') as sleep:
            resp = updater._transport(updater.LATEST_URL)
            self.assertEqual(resp.read(), b'ok')
            self.assertEqual(len(attempts), 3)
            self.assertEqual(sleep.call_count, 2)
        class HttpErrorOpener:
            def open(self, req, timeout=None):
                raise urllib.error.HTTPError(req.full_url, 404, 'not found', {}, None)
        with patch.object(updater.urllib.request, 'build_opener', return_value=HttpErrorOpener()), \
                patch.object(updater.time, 'sleep') as sleep:
            with self.assertRaises(urllib.error.HTTPError):
                updater._transport(updater.LATEST_URL)
            sleep.assert_not_called()

    def test_unified_v2_manifest_download_and_legacy_mismatch_rejected(self):
        unified_manifest = {
            'schemaVersion': 1, 'repository': trust.REPOSITORY, 'version': '1.6.0',
            'releaseTag': 'v1.6.0', 'channel': 'stable', 'minUpdaterVersion': 2,
            'assets': {'UNIFIED': {
                'name': 'OverFieldLauncher-update.zip',
                'url': trust.asset_url('v1.6.0', 'OverFieldLauncher-update.zip'),
                'size': len(self.payload),
                'sha256': hashlib.sha256(self.payload).hexdigest(),
                'files': {'launcher.py': {'size': len(self.content), 'sha256': hashlib.sha256(self.content).hexdigest()}},
            }},
        }
        def unified_transport(url):
            raw = json.dumps(unified_manifest).encode()
            if url == updater.LATEST_URL:
                names = ['launcher-update.json', 'launcher-update.sig', 'OverFieldLauncher-update.zip']
                return json.dumps({'draft': False, 'prerelease': False, 'tag_name': 'v1.6.0',
                                   'assets': [{'name': n, 'browser_download_url': trust.asset_url('v1.6.0', n)} for n in names]}).encode()
            if url.endswith('launcher-update.json'):
                return raw
            if url.endswith('launcher-update.sig'):
                return self.sign(raw)
            return self.payload
        res = updater.check_update(self.root, 'UNIFIED', '1.5.1', force=True, transport=unified_transport)
        self.assertEqual(res['status'], 'downloaded', res)
        self.assertTrue(res['stage_dir'].endswith('1.6.0-UNIFIED'))
        # Wrong edition on a unified v2 manifest must fail cleanly.
        self.assertEqual(updater.check_update(self.root, 'CN', '1.5.1', force=True, transport=unified_transport)['status'], 'error')

    def test_en_selection_and_inventory_hash(self):
        result = updater.check_update(self.root,'EN','1.3.0',transport=self.transport)
        self.assertEqual(result['status'],'downloaded',result)
        self.assertTrue(result['stage_dir'].endswith('1.4.0-EN'))
        self.assertTrue(self.calls[-1].endswith('OverFieldLauncher-EN-update.zip'))
        self.manifest['assets']['CN']['files']['launcher.py']['sha256'] = '0'*64
        self.assertEqual(self.check(force=True)['status'],'error')
        self.assertFalse((self.root/'runtime/updates/staging/1.4.0-CN').exists())

    def test_symlink_boundary(self):
        target = self.root/'outside'
        target.mkdir()
        link = self.root/'runtime'
        try:
            link.symlink_to(target,target_is_directory=True)
        except OSError:
            self.skipTest('host requires symlink privilege')
        self.assertEqual(self.check()['status'],'error')
        self.assertEqual(list(target.iterdir()),[])

if __name__ == '__main__':
    unittest.main()
