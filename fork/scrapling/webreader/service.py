"""The shared Scrapling server: one process on 127.0.0.1, token-protected, serving every
Claude session through shim.py so they all use the one browser.
    python -I -m scrapling.webreader.service
Windows (Hyper-V) reserves changing port ranges, so the server takes the first free port of a
few and writes it to data/service-port for the shim.
"""

import os
import secrets
import socket

from scrapling.webreader.paths import ROOT

PORTS = [int(os.environ["WEB_READER_PORT"])] if os.environ.get("WEB_READER_PORT") else [47615, 47625, 47635, 39615, 29615]
TOKEN_FILE = ROOT / "data" / "service-token"
PORT_FILE = ROOT / "data" / "service-port"


def token() -> str:
    if not TOKEN_FILE.is_file():
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


def current_port() -> int:
    try:
        return int(PORT_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return PORTS[0]


def _free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def main() -> None:
    from scrapling.webreader.server import build

    port = next((p for p in PORTS if _free(p)), None)
    if port is None:
        raise SystemExit(f"no free port among {PORTS}")
    wr, server = build("127.0.0.1", port, auth_token=token())
    PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    PORT_FILE.write_text(str(port), encoding="utf-8")
    server.run(transport="streamable-http", host="127.0.0.1", port=port, transport_security=wr._transport_security([f"127.0.0.1:{port}", f"localhost:{port}"]))


if __name__ == "__main__":
    main()
