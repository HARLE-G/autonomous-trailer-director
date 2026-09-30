"""Orchestration: load -> story/constraint maps -> bias filter -> plan -> verify -> repair/reject -> finalize -> reports."""
import copy
import json
from pathlib import Path

from .bias import filter_signals
from .budget import Budget, BudgetExceeded
from .constraints import build_constraint_map, policy_ids_for
from .llm import ReplayLLM, OutageLLM, AnthropicLLM, LLMUnavailable
from .logger import DecisionLog
from .package import Package, load_raw
from .planner import heuristic_proposal, normalize_proposal, build_prompt, finalize_layout
from .repair import repair_plan
from .story_map import build_story_map
from .timecode import tc_to_sec
from .verifier import verify_plan

ORDER = ["family", "young_adult", "dialect_region"]


class Context:
    def __init__(self, pkg, mode, llm, budget, log):
        self.pkg, self.mode, self.llm, self.budget, self.log = pkg, mode, llm, budget, log
        self.story = build_story_map(pkg, log)
        self.cmap = build_constraint_map(pkg, self.story)
        self.signals = {aid: filter_signals(a, pkg) for aid, a in pkg.audiences.items()}
        for aid, s in self.signals.items():
            log.event("bias_filter", "signals_filtered", audience=aid, used=[x["id"] for x in s["used"]], dropped=s["dropped"])

    def allowed_signal_ids(self, aid):
        return [s["id"] for s in self.signals[aid]["used"]]


def build_context(data_dir, mode="replay", outage=False, budget_overrides=None, raw=None, log=None, replay_dir=None):
    raw = raw or load_raw(data_dir)
    pkg = Package(raw)
    log = log or DecisionLog()
    llm = None
    if mode == "replay":
        llm = ReplayLLM(replay_dir or Path(data_dir) / "replay")
    elif mode == "live":
        try:
            llm = AnthropicLLM()
        except LLMUnavailable as e:
            log.event("llm", "live_unavailable_fallback", reason=str(e), fallback="heuristic")
    if outage:
        llm = OutageLLM()
    return Context(pkg, mode, llm, Budget(pkg.costs, budget_overrides), log)


# ------------------------------------------------------------------ finalize helpers
def derive_evidence(plan, ctx):
    pkg, cmap = ctx.pkg, ctx.cmap
    aud = pkg.audiences[plan["audience"]]
    pol = [f"policy:{p}" for p in policy_ids_for(cmap, aud["id"], plan["territories"])]
    actor_c = {r["subject"]: r["contract"] for r in cmap["rules"] if r["type"] == "actor_rights"}
    music_c = {r["subject"]: r["contract"] for r in cmap["rules"] if r["type"] == "music_rights"}
    for s in plan["segments"]:
        ev = []
        try:
            a, b = tc_to_sec(s["source_in"]), tc_to_sec(s["source_out"])
            sc = pkg.scenes[s["video"]]
            moms = pkg.moments_overlapping(sc.id, a, b)
            ev += [f"scene:{sc.id}"] + [f"moment:{m.id}" for m in moms]
            cues = pkg.cues_overlapping(sc.id, a, b)
            ev += [f"cue:{c.id}" for c in cues]
            ev += sorted({f"contract:{actor_c[pkg.actor_of(ch)]}" for m in moms for ch in m.characters if pkg.actor_of(ch) in actor_c})
            tracks = set()
            if s["audio"].get("source_music") and sc.music: tracks.add(sc.music)
            if s["audio"].get("music_override"): tracks.add(s["audio"]["music_override"])
            ev += sorted(f"contract:{music_c[t]}" for t in tracks if t in music_c)
            ev += sorted(f"music:{t}" for t in tracks)
            if cues and s["audio"].get("dialogue"): ev.append(f"track:{s['subtitle_track']}")
            ev += [f"signal:{x}" for x in s.get("signal_refs", [])]
        except Exception:
            pass
        s["evidence"] = ev + pol
    for c in plan["text_cards"]:
        c["evidence"] = [f"fact:{x}" for x in c.get("claims", [])] + pol


def collect_approvals(plan, ctx, rep, rejected_requests=()):
    ap, seen = [], set()
    def add(kind, item, why):
        if (kind, item) not in seen:
            seen.add((kind, item)); ap.append({"approver": kind, "item": item, "why": why})
    for f in rep["findings"]:
        c = f["code"]
        if c.startswith("approval_legal"): add("legal", c.split("_")[-1], "licence requires legal sign-off before use in promos")
        if c.startswith("approval_cultural"): add("cultural", c.split("_")[-1], "traditional/cultural music requires cultural reviewer sign-off")
        if c == "music_license_expiry_near": add("legal", "licence renewal", f["message"])
        if c == "spoiler_zone_adjacent": add("editorial", f"segment {f['segment']}", "footage sits in protected final-act zone")
        if c in ("dialect_track_fallback", "subtitle_track_differs_from_audience"): add("cultural", f"segment {f['segment']} subtitles", f["message"])
        if c in ("cue_cut_mid_sentence", "subtitle_fast", "reused_footage"): add("editorial", f"segment {f['segment']}", f["message"])
    if ctx.pkg.audiences[plan["audience"]]["dialect"]:
        add("cultural", "dialect subtitle track + audience promise", "dialect audience: native-speaker review of translation and framing is mandatory")
    for sc in ctx.story["description_conflicts"]:
        if any(s["video"] == sc["scene"] for s in plan["segments"]):
            add("editorial", f"rating of scene {sc['scene']}", f"human vs AI content ratings disagree {sc['dims']}; stricter used, human rating check advised")
    add("editorial", "audio stems", "assumes dialogue and music stems are separable (episode.stems_available=true); audio engineer to confirm")
    for r in rejected_requests:
        add("editorial", f"marketing request {r['id']}", "request rejected by verifier; marketing must accept the compliant alternative")
    return ap


def estimate_cost(plan, ctx, repair_calls):
    c, pkg = ctx.pkg.costs, ctx.pkg
    scenes = {s["video"] for s in plan["segments"] if s.get("video") in pkg.scenes}
    minutes = sum(pkg.scenes[x].t_out - pkg.scenes[x].t_in for x in scenes) / 60
    planner, second = c["model_calls_usd"]["planner"], c["model_calls_usd"]["verifier_second_opinion"]
    media, tool = minutes * c["media_analysis_usd_per_minute"], repair_calls * c["tool_call_usd"]
    total = round(planner + second + media + tool, 4)
    fb_tool = round(repair_calls * c["tool_call_usd"], 4)
    return ({"currency": c["currency"], "planner_model_call": planner, "verifier_second_opinion": second, "media_analysis": round(media, 4),
             "tool_calls": round(tool, 4), "total": total, "note": "estimate for a live run; replay mode incurs $0"},
            {"strategy": "heuristic planner + cached scene annotations + deterministic verifier only (no live model calls)",
             "estimated_total": fb_tool, "trade_offs": ["no creative model ideation (beat-template selection only)",
                                                         "no LLM second opinion on tone/dialect nuance; humans review instead"],
             "triggers": ["budget guard would be exceeded", "preferred model unavailable"]})


def finalize_plan(plan, ctx, initial_rep, verify_runs, rejected_requests=()):
    finalize_layout(plan)
    derive_evidence(plan, ctx)
    rep = verify_plan(plan, ctx)
    for s in plan["segments"]:
        s["risk_flags"] = sorted({f["code"] for f in rep["findings"] if f["segment"] == s["index"] and f["severity"] == "WARN"})
    plan["validation"] = {"status": rep["status"], "timeline_seconds": rep["timeline_seconds"], "checks_run": rep["checks_run"],
                          "findings": rep["findings"], "initial_status": initial_rep["status"],
                          "initial_fail_count": sum(f["severity"] == "FAIL" for f in initial_rep["findings"]),
                          "unresolved_failures": [f for f in rep["findings"] if f["severity"] == "FAIL"]}
    plan["human_approvals"] = collect_approvals(plan, ctx, rep, rejected_requests)
    plan["assumptions"] = ["Moment catalogue and content ratings are taken from supplied annotations; conservative max(human, AI) rating used",
                           "Dialogue/music stems are separable so music can be replaced without losing dialogue",
                           "Subtitle text is burned in (captions must not depend on audio alone)",
                           "Historic engagement is a hypothesis; it only contributes a small ranking weight"]
    plan["estimated_cost"], plan["fallback_plan"] = estimate_cost(plan, ctx, verify_runs)
    if rep["status"] == "REJECTED":
        plan["fallback_plan"]["reject_note"] = "Trailer could not be made valid automatically; do not publish. Human editor must resolve unresolved_failures."
    return plan


# ------------------------------------------------------------------ marketing requests
def evaluate_request(plan, ctx, req):
    t = copy.deepcopy(plan)
    t["text_cards"].append({"after_beat": "end", "text": req["text"], "claims": [], "type": "claim", "duration": 4.0})
    rep = verify_plan(t, ctx)
    idx = len(t["text_cards"]) - 1
    codes = sorted({f["code"] for f in rep["findings"] if f["card"] == idx and f["severity"] == "FAIL"})
    return {"id": req["id"], "audience": req["audience"], "text": req["text"], "accepted": not codes, "rejected_for": codes,
            "alternative": next((c["text"] for c in plan["text_cards"] if c.get("type") == "claim"), None)}


# ------------------------------------------------------------------ propose
def propose(ctx, aud, used_elsewhere):
    aid, raw = aud["id"], None
    if ctx.llm is not None:
        try:
            ctx.budget.charge("planner", note=f"plan:{aid}")
            raw = ctx.llm.complete("plan", aid, {"prompt": build_prompt(aud, ctx)})
            ctx.log.event("plan", "llm_proposal_received", audience=aid, planner=raw.get("planner"), segments=len(raw["segments"]))
        except LLMUnavailable as e:
            ctx.log.event("plan", "llm_unavailable_fallback_to_heuristic", audience=aid, reason=str(e))
        except BudgetExceeded as e:
            ctx.log.event("plan", "budget_guard_fallback_to_heuristic", audience=aid, reason=str(e))
    if raw is None:
        raw = heuristic_proposal(aud, ctx, used_elsewhere)
        ctx.log.event("plan", "heuristic_proposal", audience=aid, segments=len(raw["segments"]))
    return normalize_proposal(raw, aud, ctx)


# ------------------------------------------------------------------ run
def run(data_dir, out_dir, mode="replay", outage=False, budget_overrides=None, audiences=None):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    ctx = build_context(data_dir, mode, outage, budget_overrides)
    log, pkg = ctx.log, ctx.pkg
    for kind, units, note in (("story_extraction", 1, "story map extraction (simulated in replay)"),
                              ("media_minute", pkg.duration / 60, "episode media analysis (simulated in replay)")):
        try:
            ctx.budget.charge(kind, units, note)
        except BudgetExceeded as e:
            log.event("budget", "guard_triggered", kind=kind, reason=str(e), action="continue with cached annotations")
    _dump(out / "story_map.json", {k: v for k, v in ctx.story.items() if k not in ("spoilers", "blocked_moments", "zone_moments", "protected_scenes", "zone_start")})
    _dump(out / "spoiler_map.json", {"protected_facts": ctx.story["spoilers"], "blocked_moments": ctx.story["blocked_moments"],
                                     "protected_scenes": ctx.story["protected_scenes"], "final_act_zone_start_s": ctx.story["zone_start"],
                                     "zone_moments": ctx.story["zone_moments"], "historic_engagement_vs_safety": ctx.story["historic_engagement_vs_safety"]})
    _dump(out / "constraint_map.json", ctx.cmap)
    _dump(out / "bias_report.json", ctx.signals)

    plans, used_elsewhere, results = {}, set(), []
    for aid in (audiences or ORDER):
        aud = pkg.audiences[aid]
        log.event("promise", "audience_promise", audience=aid, promise=aud["promise"], journey=aud["emotional_journey"],
                  approved_signals=ctx.allowed_signal_ids(aid))
        plan = propose(ctx, aud, used_elsewhere)
        initial = verify_plan(plan, ctx)
        for f in initial["findings"]:
            if f["severity"] == "FAIL":
                log.event("verify", "REJECT_finding", audience=aid, code=f["code"], segment=f["segment"], card=f["card"], message=f["message"], evidence=f["evidence"])
        try:
            ctx.budget.charge("second_opinion", note=f"second opinion:{aid} (skipped in replay/heuristic; estimate only)") if ctx.mode == "live" else None
        except BudgetExceeded:
            log.event("budget", "second_opinion_skipped", audience=aid)
        repaired = repair_plan(plan, ctx, aud, used_elsewhere)
        for h in repaired.get("repair_history", []):
            log.event("repair", h["action"], audience=aid, target=h["target"], codes=h["codes"], before=h["before"], after=h["after"])
        try:
            ctx.budget.charge("tool_call", 40, f"verification tool calls:{aid}")
        except BudgetExceeded:
            pass
        rejected = [r for r in (evaluate_request(repaired, ctx, q) for q in pkg.requests if q["audience"] == aid) if not r["accepted"]]
        for r in rejected:
            log.event("marketing_request", "REJECTED", **r)
        final = finalize_plan(repaired, ctx, initial, 40, rejected)
        final["marketing_requests"] = [r for r in (evaluate_request(final, ctx, q) for q in pkg.requests if q["audience"] == aid)]
        log.event("finalize", final["validation"]["status"], audience=aid, duration=final["duration_seconds"],
                  warnings=[f["code"] for f in final["validation"]["findings"] if f["severity"] == "WARN"],
                  unresolved=[f["code"] for f in final["validation"]["unresolved_failures"]])
        used_elsewhere |= {s.get("moment_id") for s in final["segments"] if s.get("moment_id")}
        plans[aid] = final
        results.append(initial)
        _dump(out / f"{aid}_trailer.json", _ordered(final))
    div = diversity(plans)
    log.event("cross_trailer", "diversity_check", **div)
    cost = {"budget": ctx.budget.summary(), "ledger": ctx.budget.ledger, "note": "charges are simulated in replay/heuristic mode"}
    _dump(out / "cost_report.json", cost)
    log.write(out / "decision_log.jsonl")
    from .report import write_validation_report
    write_validation_report(out / "validation_report.md", ctx, plans, results, div)
    return ctx, plans


def diversity(plans):
    sets = {a: {(s.get("video"), s.get("source_in")) for s in p["segments"]} for a, p in plans.items()}
    out, ids = {}, list(sets)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = sets[ids[i]], sets[ids[j]]
            out[f"{ids[i]}~{ids[j]}"] = round(len(a & b) / max(1, len(a | b)), 2)
    return {"jaccard_footage_overlap": out, "max_allowed": 0.6, "near_identical": [k for k, v in out.items() if v > 0.6]}


def _ordered(p):
    keys = ["trailer_id", "audience", "audience_name", "territories", "duration_seconds", "objective", "audience_promise", "emotional_journey"]
    d = {k: p[k] for k in keys if k in p}
    d.update({k: v for k, v in p.items() if k not in d})
    return d


def _dump(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
