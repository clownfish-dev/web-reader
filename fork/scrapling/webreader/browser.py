"""One stealth browser on user's own profile, shared by user, every tool and every Claude session.

Why one browser: logins live in one profile (cookies, local storage, IndexedDB), and the
browser's fingerprint stays the same between runs, so sites see one steady visitor instead
of a new device each time.

Shared window (default on a PC with a desktop): the browser is a normal window user uses by hand
(open-browser.bat brings it up); whatever user logs into there, Claude can read, and the other way
round. Claude works in background tabs of that window, so user's current tab keeps the focus.
WEB_READER_SHARED_WINDOW=0 parks the window off-screen instead and shows it only for login/checks.
WEB_READER_HEADLESS=1 runs it without a window (a PC with no desktop, like Windows session 0).
"""

import asyncio
import os
import subprocess
import time
import weakref
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Optional

from scrapling.fetchers import AsyncStealthySession

from scrapling.webreader.paths import ROOT  # noqa: E402
PROFILE = Path(os.environ.get("WEB_READER_PROFILE") or ROOT / "profile")
# The browser copy with the AppContainer read grant (see README); set here so every entry point finds it.
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "browsers"))
HEADLESS = os.environ.get("WEB_READER_HEADLESS") == "1"
SHARED_WINDOW = not HEADLESS and os.environ.get("WEB_READER_SHARED_WINDOW", "1") != "0"
PARKED = {"left": -32000, "top": -32000}
SHOWN = {"left": 80, "top": 60, "width": 1280, "height": 900}
# A window a person uses must follow its own size (no fixed 1920x1080 emulation at 2x zoom)
# and the system's light/dark setting.
_HUMAN_WINDOW = {"no_viewport": True, "viewport": None, "screen": None, "device_scale_factor": None, "color_scheme": "no-override"}


def _session_options() -> dict:
    opts: dict = dict(headless=HEADLESS, block_ads=True, google_search=False)
    exe = os.environ.get("SCRAPLING_EXECUTABLE_PATH")
    if exe:
        opts["executable_path"] = exe
    return opts


def _cat_icon() -> None:
    """web-reader's cat on user's window (taskbar + title bar, its own taskbar group): tools/window-icon.ps1
    keeps it applied while the browser runs. Started through WMI so it outlives this server."""
    if os.name != "nt":
        return
    script = Path(__file__).resolve().parent / "tools" / "window-icon.ps1"
    cmd = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{script}" -Dir "{PROFILE}" -Watch'
    try:
        subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                          "Invoke-CimMethod Win32_Process -MethodName Create -Arguments @{ CommandLine = $env:WR_CMD } | Out-Null"],
                         env={**os.environ, "WR_CMD": cmd}, creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass

class SharedBrowser:
    """Starts the profile's browser on first use and restarts it if it went away (user closed it)."""

    def __init__(self) -> None:
        self._owner: Optional[AsyncStealthySession] = None
        self._lock = asyncio.Lock()
        self._tab_lock = asyncio.Lock()
        self._target_ids: "weakref.WeakKeyDictionary[Any, str]" = weakref.WeakKeyDictionary()
        self.keeper: Any = None  # the window's first tab: user's in shared mode, a blank one otherwise
        self._ua: Optional[str] = None

    @property
    def alive(self) -> bool:
        return bool(self._owner and self._owner._is_alive and self._owner.context)

    async def get(self) -> AsyncStealthySession:
        async with self._lock:
            if self.alive:
                return self._owner  # type: ignore[return-value]
            PROFILE.mkdir(parents=True, exist_ok=True)
            flags = [] if HEADLESS or SHARED_WINDOW else ["--window-position=-32000,-32000"]
            extra = {"additional_args": dict(_HUMAN_WINDOW)} if not HEADLESS else {}
            owner = AsyncStealthySession(user_data_dir=str(PROFILE), max_pages=1, extra_flags=flags, **extra, **_session_options())
            await owner.start()
            ctx = owner.context
            self.keeper = ctx.pages[0] if ctx.pages else await ctx.new_page()
            ctx.on("close", lambda *_: setattr(owner, "_is_alive", False))
            self._owner = owner
            await self.park()
            if SHARED_WINDOW:
                _cat_icon()
            return owner

    async def new_page(self) -> Any:
        """A tab for Claude, in its own window parked off-screen, so the tab user is looking at keeps the focus."""
        owner = await self.get()
        ctx = owner.context
        if HEADLESS:
            return await ctx.new_page()
        # One at a time: expect_page() takes the next new tab, so concurrent calls could swap tabs
        # (measured: read urls in parallel got "page closed" / ERR_ABORTED from shared tabs).
        # The tab is found by its target id, never "the next new tab", so a tab user opens at the same
        # moment is never taken (and later closed) by Claude.
        async with self._tab_lock:
            anchor = next((p for p in ctx.pages if not p.is_closed()), None) or await ctx.new_page()
            cdp = await ctx.new_cdp_session(anchor)
            try:
                # Its own window, parked off-screen: a background tab is not rendered, so clicks wait forever for it to be "stable"
                # (measured), while the only tab of a window renders. The window does not take the focus from user.
                tid = (await cdp.send("Target.createTarget", {"url": "about:blank", "newWindow": True, "background": True, **PARKED, "width": 1280, "height": 900}))["targetId"]
            finally:
                await cdp.detach()
            for _ in range(250):
                for p in ctx.pages:
                    if p not in self._target_ids:
                        s = await ctx.new_cdp_session(p)
                        try:
                            self._target_ids[p] = (await s.send("Target.getTargetInfo"))["targetInfo"]["targetId"]
                        finally:
                            await s.detach()
                    if self._target_ids[p] == tid:
                        return p
                await asyncio.sleep(0.02)
            raise RuntimeError("새 탭을 찾지 못했어요")

    async def user_agent(self) -> str:
        """The browser's own user agent, sent with HTTP reads so they match the browser's cookies."""
        if not self._ua:
            await self.get()
            self._ua = await self.keeper.evaluate("() => navigator.userAgent")
        return self._ua

    async def _window(self, page: Any, bounds: dict) -> None:
        if HEADLESS:
            return
        cdp = await page.context.new_cdp_session(page)
        try:
            win = await cdp.send("Browser.getWindowForTarget")
            await cdp.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": {"windowState": "normal"}})
            if bounds is not None:
                await cdp.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": bounds})
        finally:
            await cdp.detach()

    async def show(self, page: Any) -> bool:
        """Put the window on user's screen with this tab in front. False when there is no window."""
        if HEADLESS:
            return False
        # user's window stays where user put it (only un-minimized); a Claude tab's parked window comes on screen.
        await self._window(page, None if (SHARED_WINDOW and page is self.keeper) else SHOWN)
        await page.bring_to_front()
        return True

    async def park(self, page: Any = None) -> None:
        """Move a Claude tab's window back off-screen (no page: the main window, when it is not user's)."""
        if page is not None:
            if page is not self.keeper and not page.is_closed():
                await self._window(page, PARKED)
            return
        if SHARED_WINDOW:
            return  # user's window: never moved away
        if self.keeper and not self.keeper.is_closed():
            await self._window(self.keeper, PARKED)

    async def close(self) -> None:
        async with self._lock:
            if self._owner:
                await self._owner.close()
            self._owner = None
            self._ua = None


SHARED = SharedBrowser()


class _ContextView:
    """The shared context, except that new_page() makes a background tab (SharedBrowser.new_page)."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def new_page(self) -> Any:
        return await SHARED.new_page()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ctx, name)


class TabSession(AsyncStealthySession):
    """A Scrapling browser session made of tabs in the shared browser: same API as Scrapling's
    own sessions (fetch, page pool, network history), same profile and fingerprint as all others.
    Closing it closes only its tabs."""

    def __init__(self, max_pages: int = 1, record_requests: bool = False, **kwargs: Any):
        options = _session_options() | kwargs
        super().__init__(max_pages=max_pages, record_requests=record_requests, **options)

    async def start(self) -> None:
        owner = await SHARED.get()
        self.playwright = owner.playwright
        self.context = _ContextView(owner.context)
        self.network.page_filter = lambda page: any(info.page is page for info in self.page_pool.pages)
        self.network._attach(owner.context)
        self._is_alive = True

    async def close(self) -> None:
        self.network._detach()
        if self._is_alive:
            await self.close_pages()
        self.context = None
        self.playwright = None
        self._is_alive = False

    def current_page(self) -> Any:
        """The session's most recent open tab, or None."""
        for info in reversed(self.page_pool.pages):
            if not info.page.is_closed():
                return info.page
        return None
