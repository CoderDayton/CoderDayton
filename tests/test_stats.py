import datetime as dt
import pathlib
import sys
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import generate as g

D = dt.date


class CommitType(unittest.TestCase):
    def test_prefixes(self):
        self.assertEqual(g.commit_type("feat(ui): add pane"), "feat")
        self.assertEqual(g.commit_type("fix!: drop flag"), "fix")
        self.assertEqual(g.commit_type("Refactor: tidy"), "refactor")
        self.assertEqual(g.commit_type("Updated readme"), "untyped")
        self.assertEqual(g.commit_type("merge: a: b"), "merge")


class Streaks(unittest.TestCase):
    def test_longest_and_current(self):
        days = {D(2026, 1, 1), D(2026, 1, 2), D(2026, 1, 3), D(2026, 1, 10), D(2026, 1, 11)}
        self.assertEqual(g.streaks(days, D(2026, 1, 11)), (3, 2))

    def test_current_survives_quiet_today(self):
        self.assertEqual(g.streaks({D(2026, 1, 9), D(2026, 1, 10)}, D(2026, 1, 11)), (2, 2))

    def test_current_breaks_after_gap(self):
        self.assertEqual(g.streaks({D(2026, 1, 8)}, D(2026, 1, 11)), (1, 0))

    def test_empty(self):
        self.assertEqual(g.streaks(set(), D(2026, 1, 11)), (0, 0))


class Compute(unittest.TestCase):
    def test_local_time_buckets(self):
        tz = ZoneInfo("America/Chicago")
        now = dt.datetime(2026, 3, 2, 12, tzinfo=tz)
        commits = [
            {"committedDate": "2026-03-01T08:30:00Z", "messageHeadline": "feat: a", "additions": 10, "deletions": 2, "lang": "Rust"},
            {"committedDate": "2026-02-02T03:00:00Z", "messageHeadline": "wip", "additions": 1, "deletions": 1, "lang": "Python"},
        ]
        s = g.compute(commits, now)
        self.assertEqual(s["hours"][2], 1)
        self.assertEqual(s["hours"][21], 1)
        self.assertEqual(s["grid"][6][2], 1)
        self.assertEqual(s["months"], {"Jan": [0, 0], "Feb": [1, 1], "Mar": [10, 2]})
        self.assertEqual(s["types"]["feat"], 1)
        self.assertEqual(s["night"], 0.5)
        self.assertEqual(s["span"], 61)

    def test_no_commits_renders(self):
        tz = ZoneInfo("America/Chicago")
        s = g.compute([], dt.datetime(2026, 1, 1, 9, tzinfo=tz))
        cfg = {"name": "vii", "bio": "x"}
        for card in (g.header(s, cfg, ""), g.clock(s, ""), g.shape(s, "")):
            self.assertTrue(card.startswith("<svg"))


if __name__ == "__main__":
    unittest.main()
