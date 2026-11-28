"""Build portable/core update assets and sign exact release metadata.

Publisher-only tool. Requires an existing Node.js executable and a dedicated
private signing PEM passed explicitly; private key material is never packaged.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

if __package__:
    from .project_layout import PROJECT_ROOT, RUNTIME_ROOT, release_document, source_file
else:
    from project_layout import PROJECT_ROOT, RUNTIME_ROOT, release_document, source_file

sys.path.insert(0, str(RUNTIME_ROOT))


def digest(data): return hashlib.sha256(data).hexdigest()
def encoded(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')


def release_content(name, path):
    data = Path(path).read_bytes()
    if Path(name).suffix.lower() == '.md':
        data = release_document(data)
    return data


def build(version,source,templates,out,node,signing_key):
    from update_policy import is_managed,validate_files
    from update_trust import asset_url,verify_manifest,version_tuple
    version_tuple(version)
    source=Path(source).resolve();templates=Path(templates).resolve();out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    private_path=Path(signing_key).resolve()
    if any(private_path.is_relative_to(directory) for directory in (source,templates,out)):
        raise ValueError('Signing key must be outside source, templates and release assets')
    assets={};published=[]
    required_files=['launcher_updater.py','update_apply.py','update_policy.py','update_trust.py','privacy_check.py','docs/auto-update.md']
    runtime_dir=source/'src'/'runtime'
    ignored_dirs={'__pycache__','tools','output'}
    ignored_meta={'release-build.json','app/release-build.json','installation-manifest.json','app/installation-manifest.json'}
    edition='UNIFIED'
    template_path=templates/'OverFieldLauncher-portable.zip'
    if not template_path.is_file():
        template_path=templates/'OverFieldLauncher-CN-portable.zip'
    core={}
    with zipfile.ZipFile(template_path) as base:
        entries=[entry for entry in base.infolist() if not entry.is_dir()]
        uses_app=any(entry.filename.startswith('app/') for entry in entries)
        def logical_name(name):
            return name[4:] if uses_app and name.startswith('app/') else name
        def package_name(name):
            if not uses_app:return name
            if name in {'OverFieldLauncher.exe','README.md'}:return name
            if name.startswith(('docs/','backup-tool/','app/')):return name
            return 'app/'+name
        manifest_name=package_name('installation-manifest.json')
        release_build_name=package_name('release-build.json')
        for entry in entries:
            name=entry.filename
            if is_managed(name) and name not in ignored_meta:
                current=source_file(source,logical_name(name))
                core[name]=release_content(name,current) if current.is_file() else base.read(entry)
        if runtime_dir.is_dir():
            for path in sorted(runtime_dir.rglob('*')):
                if not path.is_file():continue
                rel=path.relative_to(runtime_dir).as_posix()
                parts=rel.split('/')
                if any(part in ignored_dirs for part in parts):continue
                if path.name.startswith('test_') or path.name.endswith(('.pyc','.pyo')):continue
                dest=package_name(rel)
                if dest in ignored_meta:continue
                if is_managed(dest):core[dest]=release_content(dest,path)
        for name in required_files:
            dest=package_name(name)
            if not is_managed(dest):raise ValueError('Core path not approved: '+dest)
            core[dest]=release_content(dest,source_file(source,name))
        for path in (source/'docs').glob('*.md'):
            name=path.relative_to(source).as_posix()
            if is_managed(name):core[name]=release_content(name,path)
        sdk_path=source_file(source,'LocalUidSDK.dll')
        if sdk_path.is_file():
            sdk_dest=package_name('LocalUidSDK.dll')
            if is_managed(sdk_dest):core[sdk_dest]=sdk_path.read_bytes()
        core['OverFieldLauncher.exe']=source_file(source,'OverFieldLauncher.exe').read_bytes()
        if source_file(source,'README.md').is_file():
            core['README.md']=release_content('README.md',source_file(source,'README.md'))
        file_records={name:{'size':len(data),'sha256':digest(data)} for name,data in core.items()}
        validate_files(file_records)
        baseline={'schemaVersion':1,'version':version,'edition':edition,'files':file_records}
        core[manifest_name]=encoded(baseline)
        inventory={name:{'size':len(data),'sha256':digest(data)} for name,data in core.items()}
        validate_files(inventory)
        small=out/'OverFieldLauncher-update.zip'
        with zipfile.ZipFile(small,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as target:
            for name,data in core.items():target.writestr(name,data)
        raw=small.read_bytes()
        assets[edition]={'name':small.name,'url':asset_url('v'+version,small.name),'size':len(raw),'sha256':digest(raw),'files':inventory}
        portable=out/'OverFieldLauncher-portable.zip'
        with zipfile.ZipFile(portable,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as target:
            portable_extra={}
            for name in ('backup-tool/INTEGRATION.json','backup-tool/UPSTREAM-MANIFEST.sha256'):
                p=source_file(source,name)
                if p.is_file():portable_extra[name]=p.read_bytes()
            for entry in entries:
                name=entry.filename
                if name not in core and name not in ignored_meta and name not in portable_extra:target.writestr(name,base.read(entry))
            for name,data in portable_extra.items():target.writestr(name,data)
            for name,data in core.items():target.writestr(name,data)
            target.writestr(release_build_name,encoded({'version':version,'edition':edition,'supportedLocales':['zh-CN','zh-TW','en','ja','ko'],'automaticUpdates':'signed stable unified-v2','protectedUserData':True}))
        for path,kind in [(portable,'portable'),(small,'update')]:
            with zipfile.ZipFile(path) as check:
                assert check.testzip() is None
                for name,data in core.items():assert check.read(name)==data
            published.append({'file':path.name,'edition':edition,'kind':kind,'size':path.stat().st_size,'sha256':digest(path.read_bytes())})
    manifest={'schemaVersion':1,'repository':'inori-dream/miaomiao-launcher','version':version,'releaseTag':'v'+version,'channel':'stable','minUpdaterVersion':2,'assets':assets}
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
    p.add_argument('--version',required=True);p.add_argument('--source',type=Path,default=PROJECT_ROOT)
    p.add_argument('--templates',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--node',type=Path,required=True);p.add_argument('--signing-key',type=Path,required=True)
    args=p.parse_args();build(args.version,args.source,args.templates,args.out,args.node,args.signing_key)

if __name__=='__main__':main()
