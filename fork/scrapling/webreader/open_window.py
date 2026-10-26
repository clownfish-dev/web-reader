"""Shortcut: bring up the shared logged-in browser window (start the server if needed).
    pythonw -I -m scrapling.webreader.open_window [url]
"""

import asyncio
import sys

from scrapling.webreader.proxy import HeldSession
from scrapling.webreader.shim import _connect


async def main() -> None:
    remote = HeldSession(_connect)
    try:
        await remote.call("show_browser", {"url": sys.argv[1]} if len(sys.argv) > 1 else {})
    finally:
        await remote.close()


if __name__ == "__main__":
    asyncio.run(main())
