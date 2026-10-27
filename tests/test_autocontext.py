import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from autocontext import advise as adv
from autocontext import cli
from autocontext import measure as ms
from autocontext import policy as pol_mod

FIX = os.path.join(HERE, "fixtures")
POLICY = os.path.join(FIX, "policy.toml")


def slurp(path):
    with open(path) as fh:
        return fh.read()


def fx(name):
    return os.path.join(FIX, name + ".jsonl")


def recs():
    pol = pol_mod.load(POLICY)
    sessions = [ms.parse_session(fx(n), pol.weights, pol.rediscovery_turns)
                for n in ("grow-1", "grow-2", "shrink-1", "shrink-2", "opus-1", "opus-2")]
    return pol, {r["role"]: r for r in adv.advise(pol, sessions)}


class Tests(unittest.TestCase):
    def test_measure_numbers(self):
        pol = pol_mod.load(POLICY)
        d = ms.parse_session(fx("measure-1"), pol.weights, 20).summary()
        self.assertEqual(d["turns"], 6)  # streamed duplicate message not double counted
        self.assertEqual(d["cost_total"], 4220.0)
        self.assertEqual(d["cost_per_turn"], 703.33)
        self.assertEqual((d["peak_context"], d["median_context"]), (1410, 1255))
        self.assertEqual((d["compactions"], d["commits"], d["tests_passed_lines"],
                          d["result_done_lines"]), (1, 1, 1, 1))
        self.assertEqual(d["commits_per_100_turns"], 16.67)
        self.assertEqual(d["cost_per_progress"], 1406.67)

    def test_rediscovery_detection(self):
        pol = pol_mod.load(POLICY)
        d = ms.parse_session(fx("measure-1"), pol.weights, 20).summary()
        self.assertEqual(d["rereads_after_compaction"], [1])  # a.py read again, b.py not
        short = ms.parse_session(fx("measure-1"), pol.weights, 0).summary()
        self.assertEqual(short["rereads_after_compaction"], [0])
        g = ms.parse_session(fx("grow-1"), pol.weights, 20).summary()
        self.assertEqual(g["rereads_after_compaction"], [4, 4])

    def test_advise_grows_when_rediscovery_high(self):
        _, r = recs()
        g = r["grow"]
        self.assertEqual(g["action"], "grow")
        self.assertEqual(g["recommended"], {"window": "1m", "autocompact": 400000})
        self.assertEqual(g["model_flag"], "claude-sonnet-5-5[1m]")
        self.assertEqual(g["evidence"]["rereads_per_compaction"], 4.0)
        self.assertEqual(g["evidence"]["cost_per_progress_change_pct"], 0.0)

    def test_advise_shrinks_when_cost_rises_without_progress(self):
        _, r = recs()
        s = r["shrink"]
        self.assertEqual(s["current"]["window"], "1m")
        self.assertEqual(s["action"], "shrink")
        self.assertEqual(s["recommended"], {"window": "200k", "autocompact": 150000})
        self.assertIn("no progress", s["reason"])
        self.assertEqual(s["evidence"]["rereads_per_compaction"], 4.0)  # high, yet shrink

    def test_policy_blocks_disallowed_model(self):
        _, r = recs()
        o = r["opus"]
        self.assertEqual(o["recommended"]["window"], "200k")  # evidence says grow, policy says no
        self.assertIn("may not use the 1m window", o["reason"])
        with tempfile.TemporaryDirectory() as td:
            env = os.path.join(td, "launch.env")
            body = 'ARGS="--model claude-opus-4-5[1m] --autocompact 800000"\n'
            with open(env, "w") as fh:
                fh.write(body)
            files = [fx("opus-1"), fx("opus-2")]
            rc = cli.main(["apply", env, *files, "--policy", POLICY, "--model", "claude-opus-4-5[1m]",
                           "--write"])
            self.assertEqual(rc, 3)
            self.assertEqual(slurp(env), body)
            self.assertFalse(os.path.exists(env + ".bak"))
            # the allowed model is rewritten, and only the two flags change
            with open(env, "w") as fh:
                fh.write('KEEP=1\nARGS="--model claude-sonnet-5-5 --autocompact 150000 --verbose"\n')
            rc = cli.main(["apply", env, fx("grow-1"), fx("grow-2"), "--policy", POLICY, "--write"])
            self.assertEqual(rc, 0)
            self.assertEqual(slurp(env),
                             'KEEP=1\nARGS="--model claude-sonnet-5-5[1m] --autocompact 400000 --verbose"\n')
            self.assertTrue(os.path.exists(env + ".bak"))
        out = subprocess.run([sys.executable, "-m", "autocontext", "self", "--policy", POLICY,
                              fx("opus-1"), fx("opus-2")], cwd=os.path.dirname(HERE),
                             capture_output=True, text=True, timeout=30, check=True).stdout
        self.assertEqual(out.strip(), "stay 200k: 16 re-reads after last 4 compactions")


if __name__ == "__main__":
    unittest.main()
