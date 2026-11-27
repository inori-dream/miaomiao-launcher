"""Local OverField dispatch and SDK forwarding for mitmdump."""
from urllib.parse import urlsplit
import os
from pathlib import Path

from mitmproxy import http

HOSTS = {
    "dsp-prod-of.inutan.com", "sdkapi-of.inutan.com", "sdkapi-sz.inutan.com",
    "sdkapi.inutan.com", "api-callback-of.inutan.com",
}
# Fallback seen in the installed PC client's Player.log.
DISPATCH_IPS = {"14.103.225.205", "14.103.224.57"}
LOCAL_HOST = os.environ.get('LOLO_TARGET_HOST', '127.0.0.1')
LOCAL_PORT = int(os.environ.get('LOLO_TARGET_PORT', '8080'))


def is_target(host: str, port: int, path: str) -> bool:
    host = host.lower().rstrip(".")
    if host in HOSTS:
        return True
    if host.endswith(".inutan.com") and host.startswith(("sdkapi", "dsp-", "api-callback")):
        return True
    return host in DISPATCH_IPS and port == 18881 and urlsplit(path).path.startswith("/dispatch/")


def request(flow: http.HTTPFlow) -> None:
    req = flow.request
    if req.pretty_host in ('127.0.0.1', 'localhost') and urlsplit(req.path).path == '/__lolo_proxy.pac':
        pac = os.environ.get('LOLO_PAC_PATH')
        if pac:
            flow.response = http.Response.make(200, Path(pac).read_bytes(), {'Content-Type': 'application/x-ns-proxy-autoconfig', 'Cache-Control': 'no-store'})
            return
    if not is_target(req.pretty_host, req.port, req.path):
        return
    # Keep query parameters, credentials and request/response bodies out of logs.
    flow.metadata["lolo_origin"] = f"{req.pretty_host}:{req.port}{urlsplit(req.path).path}"
    req.scheme, req.host, req.port = "http", LOCAL_HOST, LOCAL_PORT
    req.headers["host"] = f"{LOCAL_HOST}:{LOCAL_PORT}"


def response(flow: http.HTTPFlow) -> None:
    origin = flow.metadata.get("lolo_origin")
    if origin and flow.response:
        print(f"[lolo] {flow.request.method} {origin} -> {LOCAL_HOST}:{LOCAL_PORT} "
              f"status={flow.response.status_code}", flush=True)
