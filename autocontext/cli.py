"""autocontext command line: measure, advise, apply, self."""

import argparse
import difflib
import json
import os
import sys

from . import advise as adv
from . import apply as app
from . import measure as ms
from . import policy as pol_mod


def _sessions(pol, paths):
    files = pol_mod.expand_transcripts(pol, paths)
    return [ms.parse_session(p, pol.weights, pol.rediscovery_turns) for p in files]


def _fmt_session(d):
    return "\n".join([
        f"session {d['session']} (model {d['model'] or '?'})",
        f"  turns={d['turns']} cost_total={d['cost_total']} cost_per_turn={d['cost_per_turn']}",
        f"  context peak={d['peak_context']} median={d['median_context']}",
        f"  compactions={d['compactions']} rereads_after_each={d['rereads_after_compaction']}",
        (f"  commits={d['commits']} tests_passed_lines={d['tests_passed_lines']} "
         f"result_done_lines={d['result_done_lines']}"),
        (f"  per 100 turns: commits={d['commits_per_100_turns']} "
         f"tests_passed={d['tests_passed_per_100_turns']} result_done={d['result_done_per_100_turns']}"),
        f"  cost_per_progress={d['cost_per_progress']}",
    ])


def cmd_measure(a):
    pol = pol_mod.load(a.policy)
    if a.within is not None:
        pol.rediscovery_turns = a.within
    sessions = _sessions(pol, a.transcripts)
    if not sessions:
        print("no transcripts found", file=sys.stderr)
        return 2
    rows = [s.summary() for s in sessions]
    print(json.dumps(rows, indent=2) if a.json else "\n".join(_fmt_session(r) for r in rows))
    return 0


def cmd_advise(a):
    pol = pol_mod.load(a.policy)
    sessions = _sessions(pol, a.transcripts)
    if not sessions:
        print("no transcripts found", file=sys.stderr)
        return 2
    recs = adv.advise(pol, sessions)
    print(json.dumps(recs, indent=2) if a.json else "\n".join(adv.format_rec(r) for r in recs))
    return 0


def cmd_apply(a):
    pol = pol_mod.load(a.policy)
    sessions = _sessions(pol, a.transcripts)
    if not sessions:
        print("no transcripts found", file=sys.stderr)
        return 2
    recs = {r["role"]: r for r in adv.advise(pol, sessions)}
    role = a.role or (next(iter(recs)) if len(recs) == 1 else None)
    if role not in recs:
        print(f"choose --role from: {', '.join(sorted(recs))}", file=sys.stderr)
        return 2
    with open(a.file, encoding="utf-8") as fh:
        text = fh.read()
    try:
        new, notes = app.plan(pol, text, recs[role], a.model)
    except app.PolicyBlock as e:
        print(f"refused: {e}", file=sys.stderr)
        return 3
    diff = "".join(difflib.unified_diff(text.splitlines(True), new.splitlines(True),
                                        a.file, a.file + " (new)"))
    print(diff or "no change")
    for n in notes:
        print("note:", n)
    if a.write and new != text:
        app.write_with_backup(a.file, new)
        print(f"written; backup at {a.file}.bak")
    elif not a.write:
        print("dry run; pass --write to apply")
    return 0


def _default_dir():
    d = os.environ.get("AUTOCONTEXT_TRANSCRIPTS")
    if d:
        return d
    enc = os.getcwd().replace("/", "-")
    return os.path.join(os.path.expanduser("~/.claude/projects"), enc)


def cmd_self(a):
    pol = pol_mod.load(a.policy)
    paths = a.transcripts or [os.path.join(os.path.expanduser(_default_dir()), "*.jsonl")]
    sessions = _sessions(pol, paths)
    if not sessions:
        print("hold: no transcripts to measure")
        return 0
    sessions.sort(key=lambda s: (s.start, s.name))
    recs = adv.advise_role(pol, "self", sessions)
    print(adv.one_line(recs))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="autocontext", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("measure", help="numbers per session from transcripts")
    m.add_argument("transcripts", nargs="+")
    m.add_argument("--policy")
    m.add_argument("--within", type=int, help="turns after a compaction that count as re-discovery")
    m.add_argument("--json", action="store_true")
    m.set_defaults(fn=cmd_measure)

    d = sub.add_parser("advise", help="recommend window and autocompact per role")
    d.add_argument("transcripts", nargs="*")
    d.add_argument("--policy", required=True)
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=cmd_advise)

    x = sub.add_parser("apply", help="rewrite --model and --autocompact in a launcher file")
    x.add_argument("file")
    x.add_argument("transcripts", nargs="*")
    x.add_argument("--policy", required=True)
    x.add_argument("--role")
    x.add_argument("--model", help="request this model instead of the one in the file")
    x.add_argument("--dry-run", action="store_true", help="default; print the diff only")
    x.add_argument("--write", action="store_true", help="write the file, keeping a .bak")
    x.set_defaults(fn=cmd_apply)

    s = sub.add_parser("self", help="one-line advice for the agent itself")
    s.add_argument("transcripts", nargs="*")
    s.add_argument("--policy")
    s.set_defaults(fn=cmd_self)

    a = p.parse_args(argv)
    if getattr(a, "write", False) and getattr(a, "dry_run", False):
        p.error("--write and --dry-run conflict")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
