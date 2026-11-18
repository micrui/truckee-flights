#!/usr/bin/env python3
"""Download archive files through a real browser session that the maintainer verified.

Since 2026-10-29 globe.adsbexchange.com serves its history files only to a browser
session that passed the map page's verification box. This script opens the map in
Chromium with a persistent profile and downloads from inside the page, so every
request is the browser's own, with its cookies. It never touches the verification
box: when the session is not verified it says so and stops.

    tools/browser_fetch.py --login   visible window on this desktop; answer the box there
    tools/browser_fetch.py --check   private display; exit 0 if verified, 3 if not
    tools/browser_fetch.py           serve: read "url<TAB>path" lines on stdin, answer
                                     "OK<TAB>path<TAB>bytes", "404<TAB>path" or
                                     "ERR<TAB>path<TAB>reason" on stdout, one per line

pipeline.py uses serve mode when TRUCKEE_FETCH_HELPER names this file. This is the one
script in the repo with a non-stdlib import (Playwright).
"""
import asyncio, datetime, fcntl, os, random, subprocess, sys
from playwright.async_api import async_playwright

STATE = os.path.expanduser("~/.local/state/truckee-flights")
PROFILE = os.path.join(STATE, "browser-profile")
LOCK = os.path.join(STATE, "browser-profile.lock")
BASE = "https://globe.adsbexchange.com"
PROBE = "/globe_history/2026/10/01/heatmap/20.bin.ttf"
VIEWPORT = {"width": 1400, "height": 1000}
NOT_VERIFIED = "not verified: run tools/browser_fetch.py --login on the desktop"

CHECK_WAIT = 45        # seconds the unattended modes wait for the probe
LOGIN_WAIT = 600       # seconds --login waits for the person
POLL = 2               # seconds between probes
IN_FLIGHT = 2          # downloads at once
JITTER = (0.4, 1.6)    # seconds slept before each download
SETTLE = (2.0, 6.0)    # seconds slept after the page verified, before the first download
TIMEOUT = 300          # seconds one download may take

PROBE_JS = """async (u) => {
  try { const r = await fetch(u, {headers: {Range: 'bytes=0-15'}}); await r.arrayBuffer(); return r.status; }
  catch (e) { return 0; }
}"""

# The page fetches the file and keeps the body as a blob; the browser then saves the blob as a
# download, which Playwright moves into place. The bytes never pass through evaluate. (The
# browser's network record, response.body(), drops bodies over 10 MB, and a slice is 24 MB.)
FETCH_JS = """async (u) => {
  try {
    const r = await fetch(u);
    if (!r.ok) return {status: r.status, text: (await r.text()).slice(0, 300)};
    return {status: r.status, blob: URL.createObjectURL(await r.blob())};
  } catch (e) { return {status: 0, text: String(e)}; }
}"""
SAVE_JS = """(b) => { const a = document.createElement('a'); a.href = b; a.download = 'f';
  document.body.appendChild(a); a.click(); a.remove(); }"""
FREE_JS = "(b) => URL.revokeObjectURL(b)"

EXP_JS = "() => localStorage.getItem('globeTurnstileExp')"


def say(*a, err=False):
    print(*a, file=sys.stderr if err else sys.stdout, flush=True)


def start_xvfb():
    """A private X display; returns (process, ':N')."""
    r, w = os.pipe()
    proc = subprocess.Popen(["Xvfb", "-displayfd", str(w), "-screen", "0", "1440x1100x24",
                             "-nolisten", "tcp"], pass_fds=(w,),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(w)
    with os.fdopen(r) as f:
        num = f.readline().strip()
    if not num:
        stop(proc)
        sys.exit("Xvfb did not start")
    return proc, ":" + num


def stop(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


async def open_map(pw, display):
    """Launch the browser on the given X display (None: this desktop session) and open the map."""
    args = ["--no-first-run", "--password-store=basic"]
    env = None
    if display:
        env = dict(os.environ, DISPLAY=display)
        env.pop("WAYLAND_DISPLAY", None)
        args.append("--ozone-platform=x11")
    ctx = await pw.chromium.launch_persistent_context(
        PROFILE, executable_path="/usr/bin/chromium", headless=False, viewport=VIEWPORT,
        args=args, env=env)
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await page.goto(BASE + "/", wait_until="domcontentloaded", timeout=90000)
    return ctx, page


async def verified(page, seconds):
    """Probe every POLL seconds until a history file loads; True if one did within seconds."""
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    while True:
        try:
            if 200 <= await page.evaluate(PROBE_JS, PROBE) < 300:
                return True
        except Exception:
            pass  # the page navigated or is still loading; try again
        if loop.time() >= end:
            return False
        await asyncio.sleep(POLL)


async def expiry(page):
    """When the page's verification expires, as the page stored it (epoch seconds)."""
    try:
        v = int(await page.evaluate(EXP_JS) or 0)
    except Exception:
        return None
    return datetime.datetime.fromtimestamp(v).astimezone() if v > 0 else None


async def download(page, url, path):
    """Fetch url inside the page and write the decoded body to path atomically. Returns the answer line."""
    try:
        res = await asyncio.wait_for(page.evaluate(FETCH_JS, url), TIMEOUT)
    except Exception as e:
        return f"ERR\t{path}\t{first_line(e)}"
    status = res["status"]
    if status == 404:
        return f"404\t{path}"
    if not 200 <= status < 300:
        if status == 0:
            return f"ERR\t{path}\t{res['text'] or 'network error'}"
        why = f"HTTP {status}"
        if status == 403 and "verified session" in res["text"]:
            why += ": Historical data requires a verified session"
        return f"ERR\t{path}\t{why}"
    blob, tmp = res["blob"], path + ".part"
    try:
        got = asyncio.ensure_future(page.wait_for_event(
            "download", predicate=lambda d: d.url == blob, timeout=TIMEOUT * 1000))
        await asyncio.sleep(0)
        await page.evaluate(SAVE_JS, blob)
        dl = await got
        await dl.save_as(tmp)
        await dl.delete()
        os.replace(tmp, path)
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        return f"ERR\t{path}\tsave failed: {first_line(e)}"
    finally:
        try:
            await page.evaluate(FREE_JS, blob)
        except Exception:
            pass
    return f"OK\t{path}\t{os.path.getsize(path)}"


def first_line(e):
    return str(e).splitlines()[0] if str(e) else type(e).__name__


async def serve(page):
    sem = asyncio.Semaphore(IN_FLIGHT)
    loop = asyncio.get_running_loop()

    async def one(url, path):
        async with sem:
            await asyncio.sleep(random.uniform(*JITTER))
            if url.startswith(BASE + "/"):
                line = await download(page, url, path)
            else:
                line = f"ERR\t{path}\tnot a {BASE} address"
        say(line)

    tasks = []
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        if not line.strip():
            continue
        url, _, path = line.rstrip("\n").partition("\t")
        tasks.append(asyncio.ensure_future(one(url, path)))
    await asyncio.gather(*tasks)


async def main(mode):
    xvfb, display = None, None
    if mode == "login":
        if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
            say("--login needs a desktop session: neither WAYLAND_DISPLAY nor DISPLAY is set", err=True)
            return 1
        # The person's own step. The system Chromium is started plainly, as anyone would start
        # it, on the collector's profile; this script does not drive that window. They let the
        # map load, answer its verification box if it shows one, and close the window. The
        # check below then runs on a private display like any unattended run.
        say("Chromium is opening the map on the collector's profile. Let the map load, answer its")
        say("verification box if it shows one, wait until aircraft appear, then close the window.")
        subprocess.run(["/usr/bin/chromium", f"--user-data-dir={PROFILE}", "--no-first-run",
                        "--password-store=basic", "--new-window", BASE + "/"])
    xvfb, display = start_xvfb()
    try:
        async with async_playwright() as pw:
            ctx, page = await open_map(pw, display)
            try:
                if mode == "login":
                    if not await verified(page, CHECK_WAIT):
                        say("not verified: the session did not carry over from the window you closed", err=True)
                        return 3
                    exp = await expiry(page)
                    say("verified" + (f", expires {exp:%Y-%m-%d %H:%M %Z}" if exp else ""))
                    return 0
                if not await verified(page, CHECK_WAIT):
                    say(NOT_VERIFIED, err=(mode == "serve"))
                    return 3
                if mode == "check":
                    say("verified")
                    return 0
                await asyncio.sleep(random.uniform(*SETTLE))
                await serve(page)
                return 0
            finally:
                await ctx.close()
    finally:
        stop(xvfb)


if __name__ == "__main__":
    flags = sys.argv[1:]
    if flags not in ([], ["--login"], ["--check"]):
        sys.exit(__doc__)
    mode = {"--login": "login", "--check": "check"}.get(flags[0] if flags else "", "serve")
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(f"another browser_fetch.py run holds {PROFILE}")
    sys.exit(asyncio.run(main(mode)))
