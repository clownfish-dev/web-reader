"""One-time move of web-reader 0.9.4's logins into web-reader 1.0.0's profile (run by setup with the
1.0.0 server stopped). Input: Playwright state files written by the old web-reader's own
tools/export-logins.mjs. Google and banks bind logins to their browser: log in to those again with login.
    python -I -m scrapling.webreader.migrate <state.json> [<state.json> ...]
"""

import asyncio
import json
import sys

from scrapling.webreader.browser import SHARED


async def main(files: list) -> None:
    cookies = []
    for f in files:
        try:
            cookies += json.load(open(f, encoding="utf-8")).get("cookies", [])
        except (OSError, ValueError) as e:
            print(f"skip {f}: {e}")
    if not cookies:
        print("no cookies")
        return
    owner = await SHARED.get()
    await owner.context.add_cookies(cookies)
    print(f"imported {len(cookies)} cookies; profile now has {len(await owner.context.cookies())}")
    await SHARED.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
