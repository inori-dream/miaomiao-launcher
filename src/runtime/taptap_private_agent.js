/* Experimental QuickSDK adapter for an isolated LOCAL private-client process.
 * Host supplies a validated private address and UID; never attach to official client.
 * No credential interception: old QuickSDK startup-token checks are suppressed;
 * local identity is a non-official placeholder, accepted only by local server.
 */
'use strict';
const cfg = __LOCAL_CONFIGURATION__;
const keep = [];
let installed = false;
function quietGmJson(text) {
  let data;try{data=JSON.parse(text);}catch(_){return null;}
  if(!data || typeof data.open_error_log!=='boolean' || !data.open_error_log)return null;
  data.open_error_log=false;
  return JSON.stringify(data);
}
function install(mod) {
  if (installed) return;
  function api(n, ret, args) { return new NativeFunction(mod.getExportByName(n), ret, args); }
  const domainGet = api('il2cpp_domain_get', 'pointer', []);
  const assembliesGet = api('il2cpp_domain_get_assemblies','pointer',['pointer','pointer']);
  const imageGet = api('il2cpp_assembly_get_image','pointer',['pointer']);
  const classFind = api('il2cpp_class_from_name','pointer',['pointer','pointer','pointer']);
  const methodFind = api('il2cpp_class_get_method_from_name','pointer',['pointer','pointer','int']);
  const strNew = api('il2cpp_string_new','pointer',['pointer']);
  const pin = api('il2cpp_gchandle_new','uint',['pointer','uint8']);
  function utf8(s) { const p = Memory.allocUtf8String(s); keep.push(p); return p; }
  function string(s) { const p = strNew(utf8(s)); pin(p,0); return p; }
  const count = Memory.alloc(Process.pointerSize);
  count.writePointer(ptr(0));
  const domain = domainGet();
  if (domain.isNull()) return;
  const assemblies = assembliesGet(domain,count);
  const length = Process.pointerSize === 8 ? count.readU64().toNumber() : count.readU32();
  function klass(ns,name) {
    for(let i=0;i<length;i++) {
      const k=classFind(imageGet(assemblies.add(i*Process.pointerSize).readPointer()),utf8(ns),utf8(name));
      if(!k.isNull()) return k;
    }
    throw new Error('Required class unavailable: '+ns+'.'+name);
  }
  function method(k,n,arity) {
    const info=methodFind(k,utf8(n),arity);
    if(info.isNull() || info.readPointer().isNull()) throw new Error('Required method unavailable: '+n);
    return {info:info,code:info.readPointer()};
  }
  const quick=klass('quicksdk','QuickSDKImp');
  const nested=api('il2cpp_class_get_nested_types','pointer',['pointer','pointer']);
  const className=api('il2cpp_class_get_name','pointer',['pointer']);
  const parent=klass('','InitConfigAsset'), iter=Memory.alloc(Process.pointerSize);
  iter.writePointer(ptr(0));
  let config=ptr(0), child;
  while(!(child=nested(parent,iter)).isNull()) {
    if(className(child).readUtf8String()==='ConfigProperty'){config=child;break;}
  }
  if(config.isNull())throw new Error('ConfigProperty unavailable');
  const login=method(quick,'login',0), startup=method(quick,'CheckStartArgs',0);
  const success=method(quick,'onLoginSuccess',4);
  const finalServer=method(config,'get_FinalLoginServer',0);
  const invokeSuccess=new NativeFunction(success.code,'void',['pointer','pointer','pointer','pointer','pointer','pointer']);
  const uid=string(String(cfg.uid)), name=string('UID '+cfg.uid), token=string('local-session'), remaining=string('');
  const server=string('http://'+cfg.host+':'+cfg.port);
  const newLogin=new NativeCallback(function(self,mi){
    send({event:'local-login',uid:cfg.uid});
    invokeSuccess(self,uid,name,token,remaining,success.info);
  },'void',['pointer','pointer']);
  const noStartup=new NativeCallback(function(self,mi){send({event:'official-token-check-disabled'});},'void',['pointer','pointer']);
  const localServer=new NativeCallback(function(self,mi){return server;},'pointer',['pointer','pointer']);
  keep.push(newLogin,noStartup,localServer);
  Interceptor.replace(startup.code,noStartup);
  Interceptor.replace(finalServer.code,localServer);
  Interceptor.replace(login.code,newLogin);
  if (cfg.quietErrorOverlay === true) {
    // Change only the developer-window policy in the validated Steam process.
    // Keep logging and ordinary gameplay failure/disconnection dialogs intact.
    const gm = klass('', 'GMConfig'), gmData = klass('', 'GMClientConfig');
    const getField = api('il2cpp_class_get_field_from_name','pointer',['pointer','pointer']);
    const getFieldType = api('il2cpp_field_get_type','pointer',['pointer']);
    const typeKind = api('il2cpp_type_get_type','int',['pointer']);
    const objectClass = api('il2cpp_object_get_class','pointer',['pointer']);
    const setValue = api('il2cpp_field_set_value','void',['pointer','pointer','pointer']);
    const flags = api('il2cpp_method_get_flags','uint',['pointer','pointer']);
    const field = getField(gmData,utf8('open_error_log'));
    if(field.isNull() || typeKind(getFieldType(field)) !== 2)
      throw new Error('Verified Steam error-overlay boolean unavailable');
    const disabled = Memory.alloc(1);disabled.writeU8(0);keep.push(disabled);
    let reported = false;
    function quiet(data) {
      if(data.isNull()) return;
      if(!objectClass(data).equals(gmData))throw new Error('Unexpected Steam GM configuration type');
      setValue(data,field,disabled);
      if(!reported){reported=true;send({event:'error-overlay-disabled',fileLoggingPreserved:true});}
    }
    const getConfig = method(gm,'get_GmClientConfig',0), setConfig = method(gm,'set_GmClientConfig',1);
    const isStatic = (flags(setConfig.info,ptr(0)) & 0x10) !== 0;
    Interceptor.attach(setConfig.code,{onEnter(args){
      try{quiet(args[isStatic?0:1]);}catch(e){send({event:'adapter-error',message:String(e)});}
    }});
    Interceptor.attach(getConfig.code,{onLeave(result){
      try{quiet(result);}catch(e){send({event:'adapter-error',message:String(e)});}
    }});
    // IL2CPP may inline property accessors in normal game code. Also apply the
    // policy when the real asynchronous GM JSON completion delegate returns.
    const backing = getField(gm,utf8('<GmClientConfig>k__BackingField'));
    if(backing.isNull())throw new Error('Steam GM backing field unavailable');
    const staticGet = api('il2cpp_field_static_get_value','void',['pointer','pointer']);
    const getMethods = api('il2cpp_class_get_methods','pointer',['pointer','pointer']);
    const methodName = api('il2cpp_method_get_name','pointer',['pointer']);
    const paramCount = api('il2cpp_method_get_param_count','uint',['pointer']);
    const paramType = api('il2cpp_method_get_param','pointer',['pointer','uint']);
    const stringLength = api('il2cpp_string_length','int',['pointer']);
    const stringChars = api('il2cpp_string_chars','pointer',['pointer']);
    const slot = Memory.alloc(Process.pointerSize);slot.writePointer(ptr(0));keep.push(slot);
    let completions=0;
    function hookCompletion(k) {
      const methods=Memory.alloc(Process.pointerSize);methods.writePointer(ptr(0));
      let info;
      while(!(info=getMethods(k,methods)).isNull()) {
        if(methodName(info).readUtf8String().indexOf('<SyncGmClientConfig>') !== 0)continue;
        if(info.readPointer().isNull())continue;
        const callbackStatic=(flags(info,ptr(0)) & 0x10) !== 0;
        const jsonArgs=[];
        for(let p=0;p<paramCount(info);p++)if(typeKind(paramType(info,p))===14)jsonArgs.push(p+(callbackStatic?0:1));
        Interceptor.attach(info.readPointer(),{
          onEnter(args){
            // Patch only a valid local GM JSON boolean before the callback's
            // consumers run; malformed responses keep their normal error path.
            for(const p of jsonArgs){
              const original=args[p];if(original.isNull())continue;
              const size=stringLength(original);if(size<=0 || size>1048576)continue;
              const adjusted=quietGmJson(stringChars(original).readUtf16String(size));
              if(adjusted===null)continue;
              args[p]=string(adjusted);
              send({event:'gm-error-overlay-json-adjusted',otherFieldsPreserved:true});
            }
          },
          onLeave(){
            try{staticGet(backing,slot);quiet(slot.readPointer());}
            catch(e){send({event:'adapter-error',message:String(e)});}
          }
        });
        completions++;
      }
      const children=Memory.alloc(Process.pointerSize);children.writePointer(ptr(0));
      let child;
      while(!(child=nested(k,children)).isNull())hookCompletion(child);
    }
    hookCompletion(gm);
    if(!completions)throw new Error('Steam GM completion delegate unavailable');
    send({event:'error-overlay-policy-installed',scope:'validated-steam-private-copy',completionHooks:completions});
  }
  installed=true;
  send({event:'adapter-ready',host:cfg.host,port:cfg.port});
}
Process.attachModuleObserver({onAdded(mod){
 if(mod.name.toLowerCase() !== 'gameassembly.dll')return;
 const init=mod.findExportByName('il2cpp_init');
 if(!init){send({event:'adapter-error',message:'il2cpp_init export unavailable'});return;}
 Interceptor.attach(init,{onLeave(){try{install(mod);}catch(e){send({event:'adapter-error',message:String(e)});}}});
}});
