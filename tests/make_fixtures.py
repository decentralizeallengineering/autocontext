"""Regenerate tests/fixtures/*.jsonl. All data is invented."""

import json
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


class Writer:
    def __init__(self, name, model, day, cwd="/work/proj"):
        self.lines, self.n, self.model, self.day, self.cwd = [], 0, model, day, cwd
        self.name = name

    def _ts(self):
        return f"2026-01-{self.day:02d}T00:{self.n // 60:02d}:{self.n % 60:02d}Z"

    def _base(self, typ, **kw):
        d = {"type": typ, "timestamp": self._ts(), "sessionId": self.name, "cwd": self.cwd}
        d.update(kw)
        return d

    def turn(self, usage, blocks, dup=False):
        self.n += 1
        u = {"input_tokens": usage[0], "cache_read_input_tokens": usage[1],
             "cache_creation_input_tokens": usage[2], "output_tokens": usage[3]}
        msg = {"id": f"msg-{self.n}", "model": self.model, "role": "assistant",
               "content": blocks, "usage": u}
        self.lines.append(self._base("assistant", message=msg, requestId=f"req-{self.n}"))
        if dup:  # streamed duplicate of the same message
            self.lines.append(self._base("assistant", message=dict(msg), requestId=f"req-{self.n}"))

    def result(self, tid, text, error=False):
        block = {"type": "tool_result", "tool_use_id": tid, "content": text}
        if error:
            block["is_error"] = True
        self.lines.append(self._base("user", message={"role": "user", "content": [block]}))

    def read(self, usage, path):
        tid = f"tu-{self.n + 1}"
        self.turn(usage, [{"type": "tool_use", "id": tid, "name": "Read", "input": {"file_path": path}}])
        self.result(tid, "file body")

    def bash(self, usage, cmd, out):
        tid = f"tu-{self.n + 1}"
        self.turn(usage, [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd}}])
        self.result(tid, out)

    def text(self, usage, txt, dup=False):
        self.turn(usage, [{"type": "text", "text": txt}], dup=dup)

    def compact(self):
        self.lines.append(self._base("system", subtype="compact_boundary"))
        self.lines.append(self._base("user", isCompactSummary=True,
                                     message={"role": "user", "content": "Summary of earlier work."}))
        self.lines.append({"type": "summary", "summary": "Earlier work", "leafUuid": "leaf-1"})

    def save(self):
        with open(os.path.join(OUT, self.name + ".jsonl"), "w") as fh:
            fh.writelines(json.dumps(ln) + "\n" for ln in self.lines)


def measure_fixture():
    w = Writer("measure-1", "claude-sonnet-5-5", 1)
    w.lines.append({"type": "summary", "summary": "Resumed older chat", "leafUuid": "leaf-0"})
    w.read((100, 0, 1000, 50), "/work/proj/a.py")
    w.read((10, 1000, 200, 40), "/work/proj/b.py")
    w.bash((10, 1200, 100, 30), "git commit -m 'add a'", "[main 1111111] add a")
    w.bash((10, 1300, 100, 20), "python3 -m pytest -q", "3 passed in 0.10s")
    w.compact()
    w.read((500, 0, 800, 60), "/work/proj/a.py")
    w.text((10, 800, 50, 25), "All done.\nRESULT: DONE - fixture", dup=True)
    w.save()


def loop_session(name, model, day, ctx_read, progress, cost_mult=1):
    """Two compactions; after each, the four files read before are read again."""
    w = Writer(name, model, day)
    files = [f"/work/proj/f{i}.py" for i in range(1, 5)]
    u = (10, ctx_read, 1000, 200 * cost_mult)
    for rnd in range(2):
        for f in files:
            w.read(u, f)
        if progress:
            w.bash(u, f"git commit -m 'step {rnd}'", f"[main 222222{rnd}] step")
            w.bash(u, "python3 -m pytest -q", "5 passed in 0.20s")
        else:
            w.text(u, "Looking around again.")
        w.compact()
    for f in files:
        w.read(u, f)
    w.save()


def main():
    os.makedirs(OUT, exist_ok=True)
    measure_fixture()
    # grow: same cost and progress in both sessions, many re-reads
    loop_session("grow-1", "claude-sonnet-5-5", 2, 50000, True)
    loop_session("grow-2", "claude-sonnet-5-5", 3, 50000, True)
    # shrink: later session is on the large window, costs more, makes no progress
    loop_session("shrink-1", "claude-sonnet-5-5", 2, 50000, True)
    loop_session("shrink-2", "claude-sonnet-5-5", 3, 290000, False)
    # opus: identical evidence to grow, model may not use 1m
    loop_session("opus-1", "claude-opus-4-5", 2, 50000, True)
    loop_session("opus-2", "claude-opus-4-5", 3, 50000, True)


if __name__ == "__main__":
    main()
