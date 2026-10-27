"""Policy file loading (TOML, stdlib tomllib) with documented defaults."""

import fnmatch
import glob
import os
import tomllib
from dataclasses import dataclass, field

# Window tiers, lowest first. Each tier is (window label, autocompact threshold).
DEFAULT_TIERS = [("200k", 150000), ("1m", 400000), ("1m", 800000)]


@dataclass
class Policy:
    # Relative weights per token class. Unit weights mean "weighted cost = token count".
    weights: dict = field(default_factory=lambda: {
        "input": 1.0, "cache_read": 1.0, "cache_creation": 1.0, "output": 1.0})
    rediscovery_turns: int = 20      # N: turns after a compaction that count as re-discovery
    tiers: list = field(default_factory=lambda: list(DEFAULT_TIERS))
    allow_1m: list = field(default_factory=lambda: ["sonnet", "haiku"])
    allow_opus_1m: bool = False
    allow_models: list = field(default_factory=list)   # empty = any model may run at 200k
    lookback_compactions: int = 5    # most recent compactions the rule looks at
    min_compactions: int = 2         # fewer than this is not enough evidence to grow
    grow_rereads: float = 3.0        # mean re-reads per compaction needed to grow
    cost_tolerance: float = 0.10     # allowed relative rise in cost per progress unit
    transcripts: list = field(default_factory=list)    # globs
    roles: dict = field(default_factory=dict)          # name -> {match: [globs], model, tier}


def _expand(p):
    return os.path.expanduser(os.path.expandvars(p))


def load(path):
    pol = Policy()
    if not path:
        return pol
    with open(path, "rb") as fh:
        d = tomllib.load(fh)
    pol.weights.update({k: float(v) for k, v in d.get("weights", {}).items()})
    m = d.get("measure", {})
    pol.rediscovery_turns = int(m.get("rediscovery_turns", pol.rediscovery_turns))
    models = d.get("models", {})
    pol.allow_1m = [s.lower() for s in models.get("allow_1m", pol.allow_1m)]
    pol.allow_opus_1m = bool(models.get("allow_opus_1m", False))
    pol.allow_models = [s.lower() for s in models.get("allow", [])]
    if "tiers" in d:
        pol.tiers = [(t["window"], int(t["autocompact"])) for t in d["tiers"]]
    r = d.get("rule", {})
    pol.lookback_compactions = int(r.get("lookback_compactions", pol.lookback_compactions))
    pol.min_compactions = int(r.get("min_compactions", pol.min_compactions))
    pol.grow_rereads = float(r.get("grow_rereads", pol.grow_rereads))
    pol.cost_tolerance = float(r.get("cost_tolerance", pol.cost_tolerance))
    pol.transcripts = list(d.get("transcripts", []))
    pol.roles = dict(d.get("roles", {}))
    return pol


def expand_transcripts(pol, extra):
    out = []
    for pat in list(extra) + list(pol.transcripts):
        hits = sorted(glob.glob(_expand(pat)))
        out.extend(hits if hits else ([_expand(pat)] if os.path.exists(_expand(pat)) else []))
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def role_for(pol, stem, cwd_base):
    for name, spec in pol.roles.items():
        for pat in spec.get("match", []):
            if fnmatch.fnmatch(stem, pat) or (cwd_base and fnmatch.fnmatch(cwd_base, pat)):
                return name
    return "default"


def model_may_use_1m(pol, model):
    m = (model or "").lower()
    if "opus" in m and not pol.allow_opus_1m:
        return False
    return any(a in m for a in pol.allow_1m)


def model_allowed(pol, model):
    if not pol.allow_models:
        return True
    m = (model or "").lower()
    return any(a in m for a in pol.allow_models)
