"""run_day downloading through a fetch helper (TRUCKEE_FETCH_HELPER). Offline: a fake helper
speaks the line protocol of tools/browser_fetch.py; nothing touches the network.

    python3 -m unittest discover -s tests
"""
import io, json, os, struct, sys, tempfile, unittest
from contextlib import redirect_stderr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.dont_write_bytecode = True
import pipeline  # noqa: E402

HEX = 0xa1b2c3
HIT = struct.pack("<Iiihh", HEX, int(39.32e6), int(-120.14e6), 6000 // 25, 900)

# The fake helper. FAKE_CFG names a JSON file: {"exit3": bool, "missing_slot": n or null,
# "trace": "OK" | "404" | "ERR"}. Every request is logged as "pid url" to FAKE_LOG.
FAKE = r'''
import json, os, sys
cfg = json.load(open(os.environ["FAKE_CFG"]))
if cfg.get("exit3"):
    print("not verified: run tools/browser_fetch.py --login on the desktop", file=sys.stderr)
    sys.exit(3)
for line in sys.stdin:
    url, path = line.rstrip("\n").split("\t")
    open(os.environ["FAKE_LOG"], "a").write(f"{os.getpid()} {url}\n")
    if "/heatmap/" in url:
        if url.endswith(f"/{cfg.get('missing_slot')}.bin.ttf"):
            print(f"ERR\t{path}\tHTTP 403: Historical data requires a verified session", flush=True)
            continue
        body = bytes.fromhex(cfg["hit"])
    elif cfg["trace"] == "404":
        print(f"404\t{path}", flush=True)
        continue
    elif cfg["trace"] == "ERR":
        print(f"ERR\t{path}\ttimed out", flush=True)
        continue
    else:
        body = json.dumps({"timestamp": 0, "trace": []}).encode()
    open(path + ".part", "wb").write(body)
    os.replace(path + ".part", path)
    print(f"OK\t{path}\t{len(body)}", flush=True)
'''


class HelperHookTest(unittest.TestCase):
    def setUp(self):
        self.old_cwd, self.old_env = os.getcwd(), dict(os.environ)
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        with open("fake_helper.py", "w") as f:
            f.write(FAKE)
        os.environ.update(TRUCKEE_FETCH_HELPER=os.path.abspath("fake_helper.py"),
                          FAKE_CFG=os.path.abspath("cfg.json"), FAKE_LOG=os.path.abspath("log"))
        pipeline.FAILED.clear()

    def tearDown(self):
        os.chdir(self.old_cwd)
        os.environ.clear()
        os.environ.update(self.old_env)
        pipeline.FAILED.clear()
        self.tmp.cleanup()

    def run_night(self, ds, trace="OK", missing_slot=None, exit3=False):
        with open("cfg.json", "w") as f:
            json.dump({"trace": trace, "missing_slot": missing_slot, "exit3": exit3,
                       "hit": HIT.hex()}, f)
        err = io.StringIO()
        with redirect_stderr(err):
            out = pipeline.run_day(ds, window="full")
        return out, err.getvalue()

    def requests(self):
        if not os.path.exists("log"):
            return []
        with open("log") as f:
            return [l.split(" ", 1) for l in f.read().splitlines()]

    def test_downloads_through_one_helper_and_honours_cache(self):
        os.makedirs("days/2026-09-01/heatmap")
        with open("days/2026-09-01/heatmap/12.bin", "wb") as f:
            f.write(HIT)                                           # cached slice
        out, _ = self.run_night("2026-09-01")
        self.assertEqual(out, [])
        self.assertTrue(os.path.exists("days/2026-09-01/ops.json"))
        reqs = self.requests()
        urls = [u for _, u in reqs]
        self.assertEqual(len({pid for pid, _ in reqs}), 1)        # one helper for the night
        self.assertEqual(sum("/heatmap/" in u for u in urls), 20)  # 21 slots, one cached
        self.assertFalse(any(u.endswith("/heatmap/12.bin.ttf") for u in urls))
        self.assertTrue(urls[-1].endswith(f"/traces/c3/trace_full_{HEX:06x}.json"))
        os.remove("log")
        self.run_night("2026-09-01")                               # everything cached now
        self.assertEqual(self.requests(), [])

    def test_missing_slice_refused(self):
        with self.assertRaisesRegex(RuntimeError, r"heatmap slices missing: \[15\]"):
            self.run_night("2026-09-02", missing_slot=15)
        self.assertIn("verified session", str(pipeline.FAILED["days/2026-09-02/heatmap/15.bin"]))
        self.assertFalse(os.path.exists("days/2026-09-02/ops.json"))

    def test_failed_trace_refused(self):
        with self.assertRaisesRegex(RuntimeError, "trace downloads failed: a1b2c3 \\(timed out\\)"):
            self.run_night("2026-09-03", trace="ERR")
        self.assertFalse(os.path.exists("days/2026-09-03/ops.json"))

    def test_trace_404_tolerated(self):
        out, err = self.run_night("2026-09-04", trace="404")
        self.assertEqual(out, [])
        self.assertIn("HTTP 404", err)
        self.assertEqual(pipeline.FAILED[f"days/2026-09-04/traces/{HEX:06x}.json"], 404)
        self.assertTrue(os.path.exists("days/2026-09-04/ops.json"))

    def test_not_verified(self):
        with self.assertRaisesRegex(RuntimeError, "^not verified: run tools/browser_fetch.py --login"):
            self.run_night("2026-09-05", exit3=True)
        self.assertFalse(os.path.exists("days/2026-09-05/ops.json"))


if __name__ == "__main__":
    unittest.main()
