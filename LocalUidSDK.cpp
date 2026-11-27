#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <winhttp.h>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

typedef void (__cdecl *Callback)(const char*, int);
typedef int (WSAAPI *ConnectFunction)(SOCKET,const sockaddr*,int);
static ConnectFunction real_connect = nullptr;
static void log_call(const char* action);
static int WSAAPI local_connect(SOCKET socket,const sockaddr* address,int length) {
    if(address && address->sa_family==AF_INET && length>=sizeof(sockaddr_in)) {
        const sockaddr_in* input=reinterpret_cast<const sockaddr_in*>(address);
        if(ntohs(input->sin_port)==18881) {
            sockaddr_in target=*input;
            const char* host=std::getenv("LOLO_DISPATCH_HOST"); const char* port=std::getenv("LOLO_DISPATCH_PORT");
            if(host && port && InetPtonA(AF_INET,host,&target.sin_addr)==1) {
                target.sin_port=htons(static_cast<unsigned short>(std::atoi(port))); log_call("dispatch redirected to private server");
                return real_connect(socket,reinterpret_cast<const sockaddr*>(&target),sizeof(target));
            }
        }
    }
    return real_connect(socket,address,length);
}
static void hook_module(HMODULE module) {
    if(!module || !real_connect) return;
    unsigned char* base=reinterpret_cast<unsigned char*>(module);
    IMAGE_DOS_HEADER* dos=reinterpret_cast<IMAGE_DOS_HEADER*>(base);
    IMAGE_NT_HEADERS64* nt=reinterpret_cast<IMAGE_NT_HEADERS64*>(base+dos->e_lfanew);
    DWORD rva=nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress;
    if(!rva) return;
    IMAGE_IMPORT_DESCRIPTOR* imports=reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR*>(base+rva);
    for(;imports->Name;imports++) {
        if(_stricmp(reinterpret_cast<char*>(base+imports->Name),"WS2_32.dll")) continue;
        IMAGE_THUNK_DATA64* thunk=reinterpret_cast<IMAGE_THUNK_DATA64*>(base+imports->FirstThunk);
        for(;thunk->u1.Function;thunk++) if(thunk->u1.Function==reinterpret_cast<ULONGLONG>(real_connect)) {
            DWORD old=0; if(VirtualProtect(&thunk->u1.Function,sizeof(thunk->u1.Function),PAGE_READWRITE,&old)) {
                InterlockedExchangePointer(reinterpret_cast<void* volatile*>(&thunk->u1.Function),reinterpret_cast<void*>(local_connect));
                DWORD ignored=0; VirtualProtect(&thunk->u1.Function,sizeof(thunk->u1.Function),old,&ignored); log_call("private dispatch route installed");
            }
        }
    }
}
static void install_routes() {
    static LONG installed=0; if(InterlockedExchange(&installed,1)) return;
    HMODULE winsock=GetModuleHandleW(L"WS2_32.dll"); if(!winsock) winsock=LoadLibraryW(L"WS2_32.dll");
    real_connect=reinterpret_cast<ConnectFunction>(GetProcAddress(winsock,"connect"));
    hook_module(GetModuleHandleW(L"UnityPlayer.dll")); hook_module(GetModuleHandleW(L"GameAssembly.dll"));
}
static void log_call(const char* action) {
    const char* path = std::getenv("LOLO_SDK_LOG");
    if (!path) return;
    FILE* file = nullptr; fopen_s(&file, path, "ab");
    if (file) { std::fprintf(file, "local-uid-sdk %s\n", action); std::fclose(file); }
}
static bool normalize_uid(const char* input, char* output, std::size_t size) {
    if (!input || !*input || std::strlen(input) > 10) return false;
    for (const char* p=input;*p;p++) if (*p<'0'||*p>'9') return false;
    errno = 0;
    char* end = nullptr;
    unsigned long value = std::strtoul(input, &end, 10);
    if (errno || !end || *end || value == 0 || value > UINT32_MAX) return false;
    return std::snprintf(output, size, "%lu", value) > 0;
}
static int send(Callback callback, bool login) {
    char buffer[2048];
    char uid[16];
    if (!normalize_uid(std::getenv("LOLO_SDK_UID"), uid, sizeof(uid))) return -1;
    if (login)
        std::snprintf(buffer, sizeof(buffer), "{\"code\":0,\"data\":{\"message\":\"local UID login\",\"accessToken\":\"local-session\",\"uid\":\"%s\",\"userName\":\"UID %s\",\"gameId\":\"13380\",\"channelId\":\"1008\",\"gameOpenId\":\"%s\",\"avatar\":\"\"}}", uid, uid, uid);
    else
        std::snprintf(buffer, sizeof(buffer), "{\"code\":0,\"data\":{\"message\":\"local SDK ready\",\"msgdetails\":\"\"}}");
    log_call(login ? "login callback" : "ready callback");
    if (callback) callback(buffer, static_cast<int>(std::strlen(buffer)));
    return 0;
}
extern "C" {
int __cdecl SDKInit(const char*, void*, int, int, Callback cb) { install_routes(); return send(cb,false); }
int __cdecl SDKInitInLauncher(const char*, void*, int, Callback cb) { install_routes(); return send(cb,false); }
int __cdecl SDKLogin(Callback cb) { return send(cb,true); }
int __cdecl SDKLoginInLauncher(Callback cb) { return send(cb,true); }
int __cdecl SDKOfflineGameLogin(Callback cb) { return send(cb,true); }
int __cdecl SDKLoginByQRCode(Callback cb) { return send(cb,true); }
int __cdecl SDKGetUserInfo(Callback cb) { return send(cb,true); }
int __cdecl SDKGetUserInfoFromLauncher(Callback cb) { return send(cb,true); }
int __cdecl SDKStartHeartbeat(Callback cb) { return send(cb,false); }
int __cdecl SDKOpenLauncher(Callback cb) { return send(cb,false); }
int __cdecl SDKSetCrashHandler(Callback) { return 0; }
int __cdecl SDKPay(const char*, Callback cb) { const char* text="{\"code\":-1,\"data\":{\"message\":\"Payments unavailable on local server\"}}"; if(cb) cb(text,static_cast<int>(std::strlen(text))); return -1; }
int __cdecl SDKCloseAccount(const char*, Callback cb) { return send(cb,false); }
int __cdecl SDKOpenAgreementWindow(int, Callback cb) { return send(cb,false); }
int __cdecl SDKSetWindowsMode(int) { return 0; }
int __cdecl SDKUnInit() { return 0; }
int __cdecl SDKLogout() { return 0; }
int __cdecl SDKBindLauncher() { return 0; }
int __cdecl SDKBindLauncherNoParent() { return 0; }
int __cdecl SDKUnBindLauncher() { return 0; }
int __cdecl SDKGetDLCInfo() { return 0; }
int __cdecl SDKNotifyZone() { return 0; }
int __cdecl SDKShowCustomerService() { return 0; }
int __cdecl SDKTrackEvent() { return 0; }
}
