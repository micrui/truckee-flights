"""The overwrite guard. Offline: run in a temp directory with run_day, build and fetch stubbed.

    python3 -m unittest discover -s tests
"""
import datetime, io, json, os, struct, sys, tempfile, unittest
from contextlib import redirect_stdout, redirect_stderr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.dont_write_bytecode = True
import pipeline, weekly  # noqa: E402

MON = datetime.date(2026, 9, 7)
SUN = MON + datetime.timedelta(days=6)
PATH = f"data/weekly/{MON}_{SUN}.json"


def ev(date, hm="06:00", reg="N1"):
    return {"date": date, "reg": reg, "type": "C172", "cls": "piston", "own": "", "role": "", "desc": "",
            "ev": "DEP", "hm": hm, "other": "KRNO Reno", "pre7": hm < "07:00"}


def ovf(date, first="05:00", reg="N2"):
    return {"date": date, "reg": reg, "type": "C172", "cls": "piston", "own": "", "role": "", "desc": "",
            "first": first, "last": first, "dwell_min": 0, "quiet_min": 1, "min_alt": 8000,
            "min_dist": 3.0, "alt_at_closest": 8000}


class Stubs:
    """run_day and build replaced; build returns n events and n sessions per night."""
    def __init__(self, per_night):
        self.per_night, self.fetched = per_night, []

    def run_day(self, ds, window="full"):
        self.fetched.append(ds)

    def build(self, dates, window="full"):
        evs = [ev(d, f"0{5 + i}:00") for d in dates for i in range(self.per_night.get(d, 0))]
        ovs = [ovf(d, f"0{4}:{10 + i}") for d in dates for i in range(self.per_night.get(d, 0))]
        return evs, [], [], ovs, []


def stored_week(nights, n, note=None, reread=None):
    w = weekly.new_week(MON, SUN, "full", note)
    w["days"] = list(nights)
    w["events"] = [ev(d, f"0{5 + i}:00") for d in nights for i in range(n)]
    w["overflights"] = [ovf(d, f"0{4}:{10 + i}") for d in nights for i in range(n)]
    w.update(weekly.tally(w["events"], set()))
    if reread:
        w["reread"] = reread
    return w


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.old_cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        os.makedirs("data/weekly")
        self.saved = (weekly.run_day, weekly.build, weekly.render, pipeline.fetch)
        weekly.render = lambda: None

    def tearDown(self):
        weekly.run_day, weekly.build, weekly.render, pipeline.fetch = self.saved
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def stub(self, per_night):
        s = Stubs(per_night)
        weekly.run_day, weekly.build = s.run_day, s.build
        return s

    def write(self, week):
        json.dump(week, open(PATH, "w"), indent=1)
        return open(PATH, "rb").read()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                weekly.main(argv)
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue() + err.getvalue()

    def test_1_fewer_entries_refused_file_unchanged(self):
        before = self.write(stored_week(["2026-09-07", "2026-09-08"], 3))
        self.stub({"2026-09-07": 3, "2026-09-08": 1})
        code, msg = self.run_main(["--dates", "2026-09-07,2026-09-13", "--recollect"])
        self.assertNotEqual(code, 0)
        self.assertIn("2026-09-08", msg)
        self.assertIn("3 -> 1", msg)
        self.assertEqual(open(PATH, "rb").read(), before)

    def test_2_collect_fetches_only_missing_and_keeps_note_reread(self):
        rr = {"nights": ["2026-09-07"], "on": "2026-11-17", "rule": "x"}
        self.write(stored_week(["2026-09-07", "2026-09-08"], 2, note="Study week", reread=rr))
        s = self.stub({d.isoformat(): 1 for d in (MON + datetime.timedelta(days=i) for i in range(7))})
        with redirect_stdout(io.StringIO()):
            weekly.collect([MON + datetime.timedelta(days=i) for i in range(7)])
        self.assertEqual(s.fetched, [f"2026-09-{d:02d}" for d in range(9, 14)])
        w = json.load(open(PATH))
        self.assertEqual(w["note"], "Study week")
        self.assertEqual(w["reread"], rr)
        self.assertEqual(sum(1 for e in w["events"] if e["date"] == "2026-09-07"), 2)
        self.assertEqual(len(w["days"]), 7)
        self.assertFalse(w["partial"])

    def test_3_force_writes(self):
        self.write(stored_week(["2026-09-07", "2026-09-08"], 3))
        self.stub({"2026-09-07": 3, "2026-09-08": 1})
        code, msg = self.run_main(["--dates", "2026-09-07,2026-09-13", "--recollect", "--force"])
        self.assertEqual(code, 0)
        self.assertIn("losing, as allowed: night 2026-09-08", msg)
        w = json.load(open(PATH))
        self.assertEqual(sum(1 for e in w["events"] if e["date"] == "2026-09-08"), 1)

    def test_4_run_day_refuses_incomplete_download(self):
        hit = struct.pack("<Iiihh", 0xa1b2c3, int(39.32e6), int(-120.14e6), 6000 // 25, 900)

        def make_fetch(slot_missing=None, trace_code=None):
            def fake(url, path):
                if "/heatmap/" in url:
                    if slot_missing and url.endswith(f"/{slot_missing}.bin.ttf"):
                        pipeline.FAILED[path] = 503
                        return None
                    open(path, "wb").write(hit)
                    return path
                pipeline.FAILED[path] = trace_code
                return None
            return fake

        cases = [("2026-09-01", make_fetch(slot_missing=12), "heatmap slices missing"),
                 ("2026-09-02", make_fetch(trace_code=503), "trace downloads failed"),
                 ("2026-09-03", make_fetch(trace_code="timed out"), "trace downloads failed")]
        for ds, fake, words in cases:
            pipeline.fetch = fake
            with redirect_stderr(io.StringIO()), self.assertRaisesRegex(RuntimeError, words):
                pipeline.run_day(ds, window="full")
            self.assertFalse(os.path.exists(f"days/{ds}/ops.json"))
        pipeline.fetch = make_fetch(trace_code=404)
        err = io.StringIO()
        with redirect_stderr(err):
            out = pipeline.run_day("2026-09-04", window="full")
        self.assertEqual(out, [])
        self.assertIn("HTTP 404", err.getvalue())
        self.assertTrue(os.path.exists("days/2026-09-04/ops.json"))

    def test_5_reread_may_reduce(self):
        self.write(stored_week(["2026-09-07"], 3))
        os.makedirs("days/2026-09-07")
        open("days/2026-09-07/ops.json", "w").write("[]")
        self.stub({"2026-09-07": 1})
        with redirect_stdout(io.StringIO()):
            weekly.reread()
        w = json.load(open(PATH))
        self.assertEqual(w["total_ops"], 1)
        self.assertEqual(w["reread"]["nights"], ["2026-09-07"])


if __name__ == "__main__":
    unittest.main()
