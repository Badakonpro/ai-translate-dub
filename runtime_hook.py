# PyInstaller runtime hook: clear proxy env vars before imports
# This prevents httpx from trying to use SOCKS proxy during PyInstaller analysis

import os

_proxy_keys = [
    "ALL_PROXY", "all_proxy",
    "HTTP_PROXY", "http_proxy",
    "HTTPS_PROXY", "https_proxy",
    "FTP_PROXY", "ftp_proxy",
    "RSYNC_PROXY",
    "GRPC_PROXY", "grpc_proxy",
    "NO_PROXY", "no_proxy",
]

for _key in _proxy_keys:
    os.environ.pop(_key, None)
