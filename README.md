# autocontext

[![tests](https://github.com/decentralizeallengineering/autocontext/actions/workflows/tests.yml/badge.svg)](https://github.com/decentralizeallengineering/autocontext/actions/workflows/tests.yml) ![license](https://img.shields.io/badge/license-Apache--2.0-blue)

autocontext reads your Claude Code session transcripts and tells you, with the numbers behind it, whether to run the next session at a 200k or a 1m context window and where to set autocompact.

## Why

- Picking a context window and an autocompact threshold by feel either wastes tokens on a window that is too big or makes the agent re-read the same files after every compaction.
- The useful signal is already on disk: Claude Code writes every session as a JSONL transcript with token usage, compaction markers and tool calls.
- A recommendation you cannot audit is not worth following, so every answer prints the evidence it came from, and a policy file can forbid a model from using the 1m window no matter what the evidence says.

## Quick start

Standard library only; needs Python 3.11 or newer. From a checkout of this repo (the fixtures are invented sessions):

```sh
F=tests/fixtures
python3 -m autocontext measure $F/measure-1.jsonl --policy $F/policy.toml
python3 -m autocontext advise $F/grow-1.jsonl $F/grow-2.jsonl --policy $F/policy.toml
printf 'ARGS="--model claude-sonnet-5-5 --autocompact 150000 --verbose"\n' > "${TMPDIR:-/tmp}/launch.env"
python3 -m autocontext apply "${TMPDIR:-/tmp}/launch.env" $F/grow-1.jsonl $F/grow-2.jsonl --policy $F/policy.toml
python3 -m autocontext self --policy $F/policy.toml $F/grow-1.jsonl $F/grow-2.jsonl
python3 -m pytest -q
```

Expected: `measure` prints turns, cost, context peak and median, compactions and re-reads per session. `advise` prints `grow: grow -> --model claude-sonnet-5-5[1m] --autocompact 400000 (was 200k/150000)` followed by the reason and evidence. `apply` prints a diff of the launcher file and `dry run; pass --write to apply`. `self` prints `grow to 1m autocompact 400000: 16 re-reads after last 4 compactions`. Pytest reports `5 passed`.

For your own sessions, copy `policy.example.toml`, set `transcripts` to your project's transcript directory (`~/.claude/projects/<project path with / replaced by ->/*.jsonl`) and define `roles`.

## Commands

- `measure <transcripts...> [--policy P] [--within N] [--json]` prints per-session numbers.
- `advise [transcripts...] --policy P [--json]` prints one recommendation per role.
- `apply <file> [transcripts...] --policy P [--role R] [--model M] [--write]` rewrites the `--model` and `--autocompact` values in a launcher env or args file. It is a dry run that prints a diff unless `--write` is given; `--write` keeps a `.bak` copy. It exits 3 and changes nothing if the policy refuses the model or the window. It never adds a flag that is not already in the file.
- `self [transcripts...] [--policy P]` prints one line for the agent itself, using the transcripts in `$AUTOCONTEXT_TRANSCRIPTS` or, by default, `~/.claude/projects/<current directory with / replaced by ->/*.jsonl`. It prints `hold: no transcripts to measure` if there are none.

## How it decides

What is measured per session: cost is the sum over assistant turns of input, cache-read, cache-creation and output tokens times the weights in the policy (a streamed message that appears twice is counted once). A compaction is a `summary` entry, a `compact_boundary` system entry or a compact-summary user entry after the first assistant turn. A re-read is a `Read` or `NotebookRead` of a file that was already read before that compaction, within `rediscovery_turns` (default 20) turns after it. Progress is counted as three kinds of unit: `git commit` commands that did not error, tool-output lines containing `N passed`, and assistant lines starting `RESULT: DONE`. Cost per progress is cost divided by progress units.

The rule, per role (sessions are grouped by the `match` globs of each role in the policy, tested against the transcript file name and the working-directory name):

1. Grow one tier only if there are at least `min_compactions` (default 2) compactions among the most recent `lookback_compactions` (default 5), and the mean re-reads per compaction is at least `grow_rereads` (default 3), and cost per progress in the later half of the role's sessions has not risen by more than `cost_tolerance` (default 10%) over the earlier half. The comparison needs at least two sessions and progress in the earlier half.
2. Otherwise shrink one tier. High re-reads with rising cost shrinks; so does too little evidence.
3. The policy can cap the answer. Opus gets no 1m window unless `allow_opus_1m = true`; other models need a name in `models.allow_1m`. A grow that the cap forbids becomes stay, and a model outside a non-empty `models.allow` list is reported as `BLOCKED` and never applied.

Tiers default to 200k/150000, 1m/400000 and 1m/800000 (window, autocompact threshold) and can be replaced in the policy. The current tier is `roles.<name>.tier` if set, otherwise the second tier if the newest session's context exceeded 200000 tokens, otherwise the first.

## Limits

- Progress is a proxy. It counts commits, `N passed` lines and `RESULT: DONE` lines; a session with none of them has no cost per progress, and work that ends in none of them looks like zero progress.
- The weights are relative units you choose, not prices. The defaults weigh every token class equally.
- The thresholds (3 re-reads, 10%, 2 compactions, 20 turns) are defaults, not tuned values. With only one session per role, or no progress in the earlier half, the rule cannot compare cost and shrinks.
- It parses the Claude Code JSONL transcript fields the code reads (`message.usage`, `tool_use` blocks, compaction markers). A different transcript layout is read as having fewer turns or none.
- It only produces text and edits a file you point it at. It does not change a running session, and it does not check that your Claude Code version accepts the flags in your launcher file.
- Requires Python 3.11 or newer (`tomllib`).

## License

Apache-2.0. See `LICENSE`.
