"""Build portable/core update assets and sign exact release metadata.

Publisher-only tool. Requires an existing Node.js executable and a dedicated
private signing PEM passed explicitly; private key material is never packaged.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


def digest(data): return hashlib.sha256(data).hexdigest()
def encoded(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')


def build(version,source,templates,out,node,signing_key):
    from update_policy import is_managed,validate_files
    from update_trust import asset_url,verify_manifest,version_tuple
    version_tuple(version)
    source=Path(source).resolve();templates=Path(templates).resolve();out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    private_path=Path(signing_key).resolve()
    if any(private_path.is_relative_to(directory) for directory in (source,templates,out)):
        raise ValueError('Signing key must be outside source, templates and release assets')
    assets={};published=[]
    new_files=['launcher_updater.py','update_apply.py','update_policy.py','update_trust.py','docs/auto-update.md']
    for edition,binary in [('CN','OverFieldLauncher.exe'),('EN','OverFieldLauncher-EN.exe')]:
        core={}
        with zipfile.ZipFile(templates/('OverFieldLauncher-'+edition+'-portable.zip')) as base:
            for entry in base.infolist():
                name=entry.filename
                if not entry.is_dir() and is_managed(name) and name!='installation-manifest.json':
                    current=source/name
                    core[name]=current.read_bytes() if current.is_file() else base.read(entry)
            for name in new_files:
                if not is_managed(name):raise ValueError('Core path not approved: '+name)
                core[name]=(source/name).read_bytes()
            for path in (source/'docs').glob('*.md'):
                name=path.relative_to(source).as_posix()
                if is_managed(name):core[name]=path.read_bytes()
            core['OverFieldLauncher.exe']=(source/binary).read_bytes()
            if edition=='EN':
                core['README.md']=b'# OverField International Launcher '+version.encode()+b'\n\nEnglish Steam edition with signed automatic launcher updates. The updater checks the formal GitHub release, downloads only this edition\'s verified small core package and switches while game/services/capture are idle. Saves, settings, game files and runtime dependencies are protected. Local managed-file modifications pause updates rather than being overwritten.\n\nSee docs/auto-update.md and docs/collector-1012.md. Android is a separate unmodified third-party APK and is never installed or updated automatically. Read docs/android-backup.md before deciding to use it.\n'
            file_records={name:{'size':len(data),'sha256':digest(data)} for name,data in core.items()}
            validate_files(file_records)
            baseline={'schemaVersion':1,'version':version,'edition':edition,'files':file_records}
            core['installation-manifest.json']=encoded(baseline)
            inventory={name:{'size':len(data),'sha256':digest(data)} for name,data in core.items()}
            validate_files(inventory)
            small=out/('OverFieldLauncher-'+edition+'-update.zip')
            with zipfile.ZipFile(small,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as target:
                for name,data in core.items():target.writestr(name,data)
            raw=small.read_bytes()
            assets[edition]={'name':small.name,'url':asset_url('v'+version,small.name),'size':len(raw),'sha256':digest(raw),'files':inventory}
            portable=out/('OverFieldLauncher-'+edition+'-portable.zip')
            with zipfile.ZipFile(portable,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as target:
                for entry in base.infolist():
                    name=entry.filename
                    if name not in core and name!='release-build.json':target.writestr(name,base.read(entry))
                for name,data in core.items():target.writestr(name,data)
                target.writestr('release-build.json',encoded({'version':version,'edition':edition,'automaticUpdates':'signed stable core-v1','protectedUserData':True}))
            for path,kind in [(portable,'portable'),(small,'update')]:
                with zipfile.ZipFile(path) as check:
                    assert check.testzip() is None
                    for name,data in core.items():assert check.read(name)==data
                published.append({'file':path.name,'edition':edition,'kind':kind,'size':path.stat().st_size,'sha256':digest(path.read_bytes())})
    manifest={'schemaVersion':1,'repository':'inori-dream/miaomiao-launcher','version':version,'releaseTag':'v'+version,'channel':'stable','minUpdaterVersion':1,'assets':assets}
    raw=encoded(manifest);manifest_path=out/'launcher-update.json';manifest_path.write_bytes(raw)
    signature_path=out/'launcher-update.sig'
    signer="const fs=require('fs'),crypto=require('crypto');const a=process.argv.slice(1);fs.writeFileSync(a[2],crypto.sign('RSA-SHA256',fs.readFileSync(a[0]),fs.readFileSync(a[1])));"
    subprocess.run([str(node),'-e',signer,str(manifest_path),str(signing_key),str(signature_path)],check=True)
    signature=signature_path.read_bytes()
    verified=verify_manifest(raw,signature)
    assert verified['version']==version
    for path in (manifest_path,signature_path):published.append({'file':path.name,'kind':'metadata','size':path.stat().st_size,'sha256':digest(path.read_bytes())})
    (out/'release-assets.json').write_text(json.dumps({'version':version,'assets':published},indent=2),encoding='utf-8')
    print(json.dumps({'version':version,'assets':[{k:v for k,v in a.items() if k!='files'} for a in published]}))
    return manifest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version',required=True);p.add_argument('--source',type=Path,default=Path(__file__).parent)
    p.add_argument('--templates',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--node',type=Path,required=True);p.add_argument('--signing-key',type=Path,required=True)
    args=p.parse_args();build(args.version,args.source,args.templates,args.out,args.node,args.signing_key)

if __name__=='__main__':main()
