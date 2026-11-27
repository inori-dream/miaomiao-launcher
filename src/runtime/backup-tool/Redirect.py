import logging

from mitmproxy import http

logger = logging.getLogger(__name__)

ip = "127.0.0.1"
port = 21000
rd_res = False


class Redirector:
    def request(self, flow: http.HTTPFlow) -> None:
        original_host = flow.request.pretty_host
        host = original_host.rstrip(".").lower()
        if not rd_res and host == "cdn-of.inutan.com":
            return

        if host.endswith(".inutan.com"):
            logger.debug("重定向 %s → %s:%s", original_host, ip, port)
            self._redirect(flow, orig_host=original_host)

    def _redirect(self, flow: http.HTTPFlow, orig_host: str):
        original_host = orig_host
        flow.request.scheme = "http"

        flow.request.host = ip
        flow.request.port = port

        flow.request.headers["Host"] = original_host
        try:
            flow.server_conn.address = (ip, port)
        except Exception:
            pass
        flow.server_conn.tls = False


addons = [Redirector()]
