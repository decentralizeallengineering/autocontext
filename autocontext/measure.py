"""Parse Claude Code session JSONL transcripts into per-session numbers."""

import json
import os
import re
import statistics
from dataclasses import dataclass, field

READ_TOOLS = {"Read", "NotebookRead"}
COMMIT_RE = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?commit\b")
PASSED_RE = re.compile(r"\b\d+\s+(?:tests?\s+)?passed\b", re.IGNORECASE)
DONE_RE = re.compile(r"^\s*RESULT:\s*DONE\b")


@dataclass
class Session:
    path: str
    name: str
    start: str = ""
    model: str = ""
    cwd_base: str = ""
    turns: int = 0
    tokens: dict = field(default_factory=lambda: {
        "input": 0, "cache_read": 0, "cache_creation": 0, "output": 0})
    cost: float = 0.0
    ctx: list = field(default_factory=list)
    compaction_turns: list = field(default_factory=list)
    rereads: list = field(default_factory=list)   # one count per compaction
    commits: int = 0
    tests_passed: int = 0
    done: int = 0

    @property
    def progress(self):
        return self.commits + self.tests_passed + self.done

    def summary(self):
        n = self.turns
        per100 = (lambda x: round(100.0 * x / n, 2)) if n else (lambda x: 0.0)
        return {
            "session": self.name,
            "model": self.model,
            "turns": n,
            "cost_total": round(self.cost, 2),
            "cost_per_turn": round(self.cost / n, 2) if n else 0.0,
            "peak_context": max(self.ctx) if self.ctx else 0,
            "median_context": int(statistics.median(self.ctx)) if self.ctx else 0,
            "compactions": len(self.compaction_turns),
            "rereads_after_compaction": list(self.rereads),
            "commits": self.commits,
            "tests_passed_lines": self.tests_passed,
            "result_done_lines": self.done,
            "commits_per_100_turns": per100(self.commits),
            "tests_passed_per_100_turns": per100(self.tests_passed),
            "result_done_per_100_turns": per100(self.done),
            "cost_per_progress": round(self.cost / self.progress, 2) if self.progress else None,
        }


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def _tool_path(inp):
    if not isinstance(inp, dict):
        return None
    for k in ("file_path", "notebook_path", "path"):
        if isinstance(inp.get(k), str):
            return inp[k]
    return None


def _weighted(usage, weights):
    return sum(usage[k] * weights.get(k, 1.0) for k in usage)


def parse_session(path, weights, window):
    stem = os.path.splitext(os.path.basename(path))[0]
    s = Session(path=path, name=stem)
    turn_ids = {}                 # message id -> index into usages
    usages = []                   # per-turn usage dicts
    seen_tool_use = set()
    seen_done = set()
    commit_ids = set()
    reads = []                    # (turn, path)
    last_marker_turn = -1
    with open(path, encoding="utf-8", errors="replace") as fh:
        for ln, raw in enumerate(fh):
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            ts = ev.get("timestamp")
            if isinstance(ts, str) and (not s.start or ts < s.start):
                s.start = ts
            if ev.get("cwd") and not s.cwd_base:
                s.cwd_base = os.path.basename(str(ev["cwd"]).rstrip("/"))
            typ = ev.get("type")
            is_marker = (typ == "summary"
                         or (typ == "system" and ev.get("subtype") == "compact_boundary")
                         or (typ == "user" and ev.get("isCompactSummary") is True))
            if is_marker:
                # Markers before the first assistant turn describe an earlier
                # conversation, not a compaction of this one.
                if usages and last_marker_turn != len(usages):
                    s.compaction_turns.append(len(usages))
                    last_marker_turn = len(usages)
                continue
            msg = ev.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if typ == "assistant":
                key = msg.get("id") or f"line{ln}"
                u = msg.get("usage") or {}
                cur = {
                    "input": int(u.get("input_tokens") or 0),
                    "cache_read": int(u.get("cache_read_input_tokens") or 0),
                    "cache_creation": int(u.get("cache_creation_input_tokens") or 0),
                    "output": int(u.get("output_tokens") or 0),
                }
                if key in turn_ids:
                    usages[turn_ids[key]] = cur
                else:
                    turn_ids[key] = len(usages)
                    usages.append(cur)
                turn = turn_ids[key]
                if msg.get("model") and msg["model"] != "<synthetic>":
                    s.model = msg["model"]
                if isinstance(content, list):
                    for b in content:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "tool_use":
                            tid = b.get("id") or f"{ln}-{id(b)}"
                            if tid in seen_tool_use:
                                continue
                            seen_tool_use.add(tid)
                            inp = b.get("input")
                            if b.get("name") in READ_TOOLS and _tool_path(inp):
                                reads.append((turn, _tool_path(inp)))
                            if b.get("name") == "Bash" and isinstance(inp, dict) \
                                    and COMMIT_RE.search(str(inp.get("command", ""))):
                                s.commits += 1
                                commit_ids.add(tid)
                        elif b.get("type") == "text":
                            for line in str(b.get("text", "")).splitlines():
                                if DONE_RE.match(line) and (turn, line) not in seen_done:
                                    seen_done.add((turn, line))
                                    s.done += 1
            elif typ == "user" and isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict) or b.get("type") != "tool_result":
                        continue
                    if b.get("is_error") and b.get("tool_use_id") in commit_ids:
                        commit_ids.discard(b["tool_use_id"])
                        s.commits -= 1
                    for line in _text_of(b.get("content")).splitlines():
                        if PASSED_RE.search(line):
                            s.tests_passed += 1
    s.turns = len(usages)
    for u in usages:
        for k, v in u.items():
            s.tokens[k] += v
        s.cost += _weighted(u, weights)
        s.ctx.append(u["input"] + u["cache_read"] + u["cache_creation"])
    ends = s.compaction_turns[1:] + [10 ** 9]
    for i, c in enumerate(s.compaction_turns):
        before = {p for t, p in reads if t < c}
        hi = min(c + window, ends[i])
        s.rereads.append(sum(1 for t, p in reads if c <= t < hi and p in before))
    return s
