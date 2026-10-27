"""Rewrite only the --model and --autocompact flags in a launcher env or args file."""

import os
import re
import shutil

from . import policy as pol_mod
from .advise import strip_1m

MODEL_RE = re.compile(r"(--model)(=|\s+)([^\s\"']+)")
COMPACT_RE = re.compile(r"(--autocompact)(=|\s+)(\d+)")


class PolicyBlock(Exception):
    pass


def plan(pol, text, rec, requested_model=None):
    """Return (new_text, notes). Raises PolicyBlock for a model the policy refuses."""
    notes = []
    if rec["action"] == "blocked":
        raise PolicyBlock(rec["reason"])
    m = MODEL_RE.search(text)
    file_model = m.group(3) if m else None
    base = strip_1m(requested_model or file_model or rec["model"])
    if not pol_mod.model_allowed(pol, base):
        raise PolicyBlock(f"model {base} is not in the policy allow list")
    want_1m = rec["recommended"]["window"] == "1m"
    if requested_model and requested_model.endswith("[1m]") and not pol_mod.model_may_use_1m(pol, base):
        raise PolicyBlock(f"model {base} may not use the 1m window under this policy")
    if want_1m and not pol_mod.model_may_use_1m(pol, base):
        raise PolicyBlock(f"model {base} may not use the 1m window under this policy")
    new_model = base + ("[1m]" if want_1m else "")
    new_compact = str(rec["recommended"]["autocompact"])

    def sub_model(mo):
        return f"{mo.group(1)}{mo.group(2)}{new_model}"

    def sub_compact(mo):
        return f"{mo.group(1)}{mo.group(2)}{new_compact}"

    out, n1 = MODEL_RE.subn(sub_model, text)
    out, n2 = COMPACT_RE.subn(sub_compact, out)
    if not n1:
        notes.append("no --model flag in file; not added")
    if not n2:
        notes.append("no --autocompact flag in file; not added")
    return out, notes


def write_with_backup(path, new_text):
    shutil.copy2(path, path + ".bak")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    shutil.copymode(path, tmp)
    os.replace(tmp, path)
