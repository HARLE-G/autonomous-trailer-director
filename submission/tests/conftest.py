import json
import shutil
from pathlib import Path

import pytest

from trailer_director.pipeline import build_context
from trailer_director.planner import normalize_proposal

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture()
def ctx():
    return build_context(DATA, mode="heuristic")


def make_plan(ctx, aud_id, segs, cards=None):
    """Build a plan from raw proposal segments (same path the LLM/replay output takes)."""
    aud = ctx.pkg.audiences[aud_id]
    cards = cards if cards is not None else [{"after_beat": "end", "text": "The Lighthouse Letter", "claims": [], "type": "title"}]
    return normalize_proposal({"planner": "test", "segments": segs, "text_cards": cards}, aud, ctx)


def codes(rep, sev="FAIL", seg=None):
    return {f["code"] for f in rep["findings"] if f["severity"] == sev and (seg is None or f["segment"] == seg)}


# a padding set of clean footage so duration/other checks do not drown the assertion under test
CLEAN_FAMILY = [{"beat": "hook", "moment_id": "s01b"}, {"beat": "setup", "moment_id": "s01a"}, {"beat": "spark", "moment_id": "s04a"},
                {"beat": "mystery", "moment_id": "s02b"}, {"beat": "heart", "moment_id": "s05b"}, {"beat": "turn", "moment_id": "s04b"},
                {"beat": "payoff", "moment_id": "s02a"}]
