import json

from conftest import make_plan, codes, CLEAN_FAMILY, DATA
from trailer_director.bias import filter_signals
from trailer_director.budget import Budget, BudgetExceeded
from trailer_director.changes import apply_change_raw
from trailer_director.pipeline import build_context, run, evaluate_request
from trailer_director.repair import repair_plan
from trailer_director.timecode import tc_to_sec, sec_to_tc
from trailer_director.verifier import verify_plan
import pytest


def test_clickbait_and_invented_relationship_rejected(ctx):
    cards = [{"after_beat": "end", "text": "YOU WON'T BELIEVE WHO THEIR FATHER IS?!", "claims": [], "type": "claim"}]
    rep = verify_plan(make_plan(ctx, "young_adult", CLEAN_FAMILY, cards), ctx)
    assert {"clickbait_misrepresentation", "unsupported_relationship", "unreferenced_claim"} <= codes(rep)


def test_marketing_clickbait_request_is_refused_with_alternative(tmp_path):
    ctx, plans = run(DATA, tmp_path, mode="replay")
    req = ctx.pkg.requests[0]
    res = evaluate_request(plans["young_adult"], ctx, req)
    assert not res["accepted"] and res["alternative"]
    assert any(e["decision"] == "REJECTED" and e["stage"] == "marketing_request" for e in ctx.log.events)


def test_dialect_subtitle_that_changes_relationship_is_caught(ctx):
    rep = verify_plan(make_plan(ctx, "dialect_region", [{"beat": "b", "moment_id": "s07b", "subtitle_track": "dialect_a"}]), ctx)
    assert "subtitle_relationship_mismatch" in codes(rep)
    ok = verify_plan(make_plan(ctx, "dialect_region", [{"beat": "b", "moment_id": "s07b", "subtitle_track": "dialect_b"}]), ctx)
    assert "subtitle_relationship_mismatch" not in codes(ok)


def test_stereotype_language_rejected(ctx):
    cards = [{"after_beat": "end", "text": "Rustic village charm meets mystery.", "claims": ["F03"], "type": "claim"}]
    assert "stereotype_language" in codes(verify_plan(make_plan(ctx, "dialect_region", CLEAN_FAMILY, cards), ctx))


def test_biased_audience_signals_are_filtered_and_cannot_justify_segments(ctx):
    used = {s["id"] for s in ctx.signals["dialect_region"]["used"]}
    assert "sig_dr_3" not in used and "sig_dr_4" not in used          # region-only proxy and tiny sample
    assert "sig_ya_3" not in {s["id"] for s in ctx.signals["young_adult"]["used"]}
    seg = {"beat": "b", "moment_id": "s02b", "subtitle_track": "dialect_a", "signal_refs": ["sig_dr_3"]}
    assert "unapproved_signal" in codes(verify_plan(make_plan(ctx, "dialect_region", [seg]), ctx))


def test_unverifiable_claim_and_unreadable_card_rejected(ctx):
    cards = [{"after_beat": "end", "text": "A very long line of text that nobody can read in time.", "claims": ["F99"], "type": "claim", "duration": 1.0}]
    c = codes(verify_plan(make_plan(ctx, "young_adult", CLEAN_FAMILY, cards), ctx))
    assert {"unsupported_claim", "text_card_too_fast"} <= c


def test_repair_never_forces_a_failing_trailer_through(ctx):
    raw = ctx.pkg.raw
    for cid in ("C-A01", "C-A02", "C-A03"):        # lead cast no longer cleared for promos -> nothing valid can be assembled
        raw = apply_change_raw(raw, {"id": "x", "type": "contract_update", "contract_id": cid, "patch": {"promo_allowed": False}})
    ctx2 = build_context(DATA, "heuristic", raw=raw)
    aud = ctx2.pkg.audiences["family"]
    plan = make_plan(ctx2, "family", CLEAN_FAMILY)
    fixed = repair_plan(plan, ctx2, aud)
    assert verify_plan(fixed, ctx2)["status"] == "REJECTED"


def test_outage_falls_back_to_heuristic_planner_and_still_verifies(tmp_path):
    ctx, plans = run(DATA, tmp_path, mode="replay", outage=True)
    assert all(p["provenance"]["planner"] == "heuristic" for p in plans.values())
    assert all(p["validation"]["status"] != "REJECTED" for p in plans.values())
    assert any(e["decision"] == "llm_unavailable_fallback_to_heuristic" for e in ctx.log.events)


def test_budget_guard():
    from trailer_director.package import load_raw
    costs = load_raw(DATA)["costs"]
    b = Budget(costs, {"max_model_calls": 1})
    b.charge("planner")
    with pytest.raises(BudgetExceeded):
        b.charge("planner")
    with pytest.raises(BudgetExceeded):
        Budget(costs, {"total_usd": 0.01}).charge("planner")


def test_end_to_end_replay_is_valid_distinct_and_deterministic(tmp_path):
    _, a = run(DATA, tmp_path / "a", mode="replay")
    _, b = run(DATA, tmp_path / "b", mode="replay")
    for aid, p in a.items():
        assert p["validation"]["status"] in ("PASS", "PASS_WITH_WARNINGS") and not p["validation"]["unresolved_failures"]
        assert p["validation"]["initial_status"] == "REJECTED"        # the recorded LLM drafts really were bad
        assert all(s["reason"] and s["evidence"] for s in p["segments"])
        assert p["human_approvals"] and p["estimated_cost"]["total"] > 0 and p["fallback_plan"]
    sets = [{(s["video"], s["source_in"]) for s in p["segments"]} for p in a.values()]
    for i in range(3):
        for j in range(i + 1, 3):
            assert len(sets[i] & sets[j]) / len(sets[i] | sets[j]) <= 0.6
    assert (tmp_path / "a" / "decision_log.jsonl").read_text() == (tmp_path / "b" / "decision_log.jsonl").read_text()
    for aid in a:
        assert a[aid]["segments"] == b[aid]["segments"]
    # no final segment reveals a spoiler or references a non-existent scene
    ctx = build_context(DATA, "heuristic")
    for p in a.values():
        assert all(s["video"] in ctx.pkg.scenes for s in p["segments"])
        assert not ({s.get("moment_id") for s in p["segments"]} & set(ctx.story["blocked_moments"]))


def test_timecode_roundtrip():
    assert sec_to_tc(tc_to_sec("00:02:14.200")) == "00:02:14.200"
