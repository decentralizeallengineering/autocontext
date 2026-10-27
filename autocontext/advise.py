"""The advise rule. See README for the plain statement.

Grow one tier only if, over the most recent compactions of a role,
  1. there are at least `min_compactions` of them,
  2. mean re-reads per compaction >= `grow_rereads`, and
  3. cost per progress unit in the recent half of the role's sessions has not
     risen by more than `cost_tolerance` over the earlier half (and both
     halves have progress to compare).
Otherwise shrink one tier. A tier that needs the 1m window is only granted to
a model the policy allows to use it.
"""

from collections import defaultdict

from . import policy as pol_mod


def strip_1m(model):
    return (model or "").replace("[1m]", "")


def _cpp(sessions):
    cost = sum(s.cost for s in sessions)
    prog = sum(s.progress for s in sessions)
    return cost, prog, (cost / prog if prog else None)


def _tier_cap(pol, model):
    """Highest tier index the model may use."""
    ok_1m = pol_mod.model_may_use_1m(pol, model)
    cap = 0
    for i, (win, _) in enumerate(pol.tiers):
        if win != "1m" or ok_1m:
            cap = i
    return cap


def group_by_role(pol, sessions):
    groups = defaultdict(list)
    for s in sessions:
        groups[pol_mod.role_for(pol, s.name, s.cwd_base)].append(s)
    for g in groups.values():
        g.sort(key=lambda s: (s.start, s.name))
    return dict(groups)


def advise_role(pol, role, sessions):
    spec = pol.roles.get(role, {})
    latest = sessions[-1]
    model = strip_1m(spec.get("model") or latest.model)
    tiers = pol.tiers
    if "tier" in spec:
        cur = int(spec["tier"])
    else:
        cur = 1 if max(latest.ctx or [0]) > 200000 and len(tiers) > 1 else 0
    cur = max(0, min(cur, len(tiers) - 1))
    cap = _tier_cap(pol, model)

    # Evidence: most recent compactions across the role's sessions.
    flat = [r for s in sessions for r in s.rereads][-pol.lookback_compactions:]
    n_comp = len(flat)
    total_rr = sum(flat)
    mean_rr = total_rr / n_comp if n_comp else 0.0

    change = None
    c_early = c_recent = None
    trend_known = False
    rose = False
    if len(sessions) >= 2:
        half = len(sessions) // 2
        _, _, c_early = _cpp(sessions[:half])
        r_cost, _, c_recent = _cpp(sessions[half:])
        if c_early is not None:
            trend_known = True
            if c_recent is None:
                rose = r_cost > 0           # cost spent, no progress at all
            else:
                change = c_recent / c_early - 1.0
                rose = change > pol.cost_tolerance

    evidence = {
        "sessions": len(sessions),
        "compactions_considered": n_comp,
        "rereads_total": total_rr,
        "rereads_per_compaction": round(mean_rr, 2),
        "cost_per_progress_earlier": None if c_early is None else round(c_early, 2),
        "cost_per_progress_recent": None if c_recent is None else round(c_recent, 2),
        "cost_per_progress_change_pct": None if change is None else round(100 * change, 1),
        "peak_context": max(max(s.ctx or [0]) for s in sessions),
    }
    rec = {"role": role, "model": model, "evidence": evidence,
           "current": {"window": tiers[cur][0], "autocompact": tiers[cur][1], "tier": cur}}

    if not pol_mod.model_allowed(pol, model):
        rec.update(action="blocked", target_tier=cur,
                   reason=f"model {model} is not in the policy allow list; nothing is applied")
        return rec

    high = n_comp >= pol.min_compactions and mean_rr >= pol.grow_rereads
    if high and trend_known and not rose:
        target, action = cur + 1, "grow"
        reason = (f"{total_rr} re-reads over {n_comp} compactions "
                  f"({mean_rr:.1f} each, needs {pol.grow_rereads:g}) and cost per progress did not rise")
        if target > len(tiers) - 1:
            target, action, reason = cur, "stay", reason + "; already at the largest tier"
        elif target > cap:
            target, action = cur, "stay"
            reason += f"; blocked by policy: {model} may not use the 1m window"
    else:
        target, action = cur - 1, "shrink"
        if rose:
            reason = ("cost per progress rose "
                      + ("with no progress in the recent sessions" if c_recent is None
                         else f"{100 * change:.1f}% (tolerance {100 * pol.cost_tolerance:.0f}%)"))
        elif n_comp < pol.min_compactions:
            reason = f"only {n_comp} compactions measured, needs {pol.min_compactions} to grow"
        elif mean_rr < pol.grow_rereads:
            reason = f"{total_rr} re-reads over {n_comp} compactions ({mean_rr:.1f} each, needs {pol.grow_rereads:g})"
        else:
            reason = "no earlier sessions with progress to compare cost against"
        if target < 0:
            target, action = 0, "stay"
    if cur > cap and target > cap:
        target, action = cap, "shrink"
        reason += f"; policy: {model} may not use the 1m window"
    rec.update(action=action, target_tier=target, reason=reason)
    rec["recommended"] = {"window": tiers[target][0], "autocompact": tiers[target][1]}
    rec["model_flag"] = model + ("[1m]" if tiers[target][0] == "1m" else "")
    return rec


def advise(pol, sessions):
    return [advise_role(pol, r, g) for r, g in sorted(group_by_role(pol, sessions).items())]


def format_rec(rec):
    ev = rec["evidence"]
    if rec["action"] == "blocked":
        return f"{rec['role']}: BLOCKED - {rec['reason']}"
    r = rec["recommended"]
    head = (f"{rec['role']}: {rec['action']} -> --model {rec['model_flag']} --autocompact {r['autocompact']}"
            f" (was {rec['current']['window']}/{rec['current']['autocompact']})")
    lines = [head,
             f"  why: {rec['reason']}",
             "  evidence: " + ", ".join(f"{k}={v}" for k, v in ev.items())]
    return "\n".join(lines)


def one_line(rec):
    """Single line for the agent to read."""
    if rec["action"] == "blocked":
        return f"blocked: {rec['reason']}"
    ev = rec["evidence"]
    r = rec["recommended"]
    n = ev["compactions_considered"]
    tail = (f"{ev['rereads_total']} re-reads after last {n} compactions" if n
            else "no compactions measured")
    if rec["action"] == "stay":
        return f"stay {r['window']}: {tail}"
    return f"{rec['action']} to {r['window']} autocompact {r['autocompact']}: {tail}"
