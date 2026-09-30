"""Planner: audience promise -> beat-by-beat selection -> segment objects.

The planner is creative, NOT authoritative: it only pre-filters obvious story-map blocks and dislikes
audience 'avoid' themes. It deliberately does not certify rights/ratings; the verifier decides.
"""
from .timecode import sec_to_tc


def plan_skeleton(aud, pkg):
    return {"trailer_id": f"{aud['id']}_v1", "audience": aud["id"], "audience_name": aud["name"],
            "territories": aud["territories"], "objective": aud["objective"], "audience_promise": aud["promise"],
            "emotional_journey": aud["emotional_journey"]}


def make_segment(m, pkg, track, beat, reason, signals=()):
    cues = pkg.cues_overlapping(m.scene, m.t_in, m.t_out)
    texts = [pkg.cue_text(c, track) for c in cues]
    return {"beat": beat, "moment_id": m.id, "video": m.scene, "source_in": sec_to_tc(m.t_in), "source_out": sec_to_tc(m.t_out),
            "audio": {"dialogue": bool(cues), "source_music": True, "music_override": None, "voice_over": None},
            "subtitle_track": track, "subtitle": " ".join(t for t in texts if t), "cue_ids": [c.id for c in cues],
            "transition": "cut", "reason": reason, "signal_refs": list(signals), "risk_flags": []}


def signals_for(m, ctx, aud_id):
    return [s["id"] for s in ctx.signals[aud_id]["used"] if set(s["preference_tags"]) & set(m.themes)]


def beat_score(m, beat, aud, ctx, used_elsewhere=()):
    match = len(set(beat["themes"]) & set(m.themes))
    if not match:
        return None
    penal = 2 * len(set(aud["avoid_themes"]) & set(m.themes))
    sig = len(signals_for(m, ctx, aud["id"]))
    hist = 0.5 * ctx.pkg.history.get(m.scene, 0)      # engagement = hypothesis, small weight
    return 3 * match + sig + hist - penal - (1.5 if m.id in used_elsewhere else 0)


def heuristic_proposal(aud, ctx, used_elsewhere=()):
    pkg, story = ctx.pkg, ctx.story
    blocked = set(story["blocked_moments"]) | set(story["zone_moments"])
    used, segs = set(), []
    for beat in aud["beats"]:
        cands = []
        for m in pkg.moments.values():
            if m.id in used or m.id in blocked:
                continue
            sc = beat_score(m, beat, aud, ctx, used_elsewhere)
            if sc is not None:
                cands.append((-sc, m.id, m))
        if not cands:
            continue
        cands.sort(key=lambda x: (x[0], x[1]))
        m = cands[0][2]
        used.add(m.id)
        segs.append(make_segment(m, pkg, aud["subtitle_track"], beat["name"],
                                 f"Beat '{beat['name']}': {m.summary}", signals_for(m, ctx, aud["id"])))
    return {"planner": "heuristic", "segments": segs, "text_cards": [dict(c) for c in aud["cards"]]}


def normalize_proposal(raw, aud, ctx):
    """Turn a raw (LLM/replay) proposal into a plan. Never corrects invalid data: bad timecodes/scenes stay bad."""
    pkg = ctx.pkg
    plan = plan_skeleton(aud, pkg)
    segs = []
    for r in raw["segments"]:
        m = pkg.moments.get(r.get("moment_id", ""))
        track = r.get("subtitle_track", aud["subtitle_track"])
        if m:
            s = make_segment(m, pkg, track, r.get("beat", "?"), r.get("reason", ""), r.get("signal_refs", []))
            if "audio" in r:
                s["audio"] = {**s["audio"], **r["audio"]}
        else:
            s = {"beat": r.get("beat", "?"), "video": r.get("video"), "source_in": r.get("source_in"), "source_out": r.get("source_out"),
                 "audio": r.get("audio", {"dialogue": False, "source_music": False, "music_override": None, "voice_over": None}),
                 "subtitle_track": track, "subtitle": r.get("subtitle", ""), "cue_ids": [], "transition": "cut",
                 "reason": r.get("reason", ""), "signal_refs": r.get("signal_refs", []), "risk_flags": []}
        s["transition"] = r.get("transition", "cut")
        segs.append(s)
    plan["segments"] = segs
    cards = [dict(c) for c in raw.get("text_cards", [])]
    plan["text_cards"] = cards
    plan["provenance"] = {"planner": raw.get("planner", "unknown")}
    finalize_layout(plan)
    return plan


def finalize_layout(plan):
    """Fill card durations, transitions, indices and declared duration (layout only; no validation here)."""
    for c in plan["text_cards"]:
        if "duration" not in c:
            c["duration"] = max(2.0, round(int(len(c["text"]) / 15 * 2 + 0.999) / 2 + 0.5, 1))
    for i, s in enumerate(plan["segments"]):
        s["index"] = i
        if i == 0:
            s["transition"] = "fade_in_from_black"
        elif i == len(plan["segments"]) - 1:
            s["transition"] = "cut_to_card"
        elif s.get("transition") in (None, "fade_in_from_black", "cut_to_card"):
            s["transition"] = "cut"
    total = 0.0
    from .timecode import tc_to_sec
    for s in plan["segments"]:
        try:
            total += tc_to_sec(s["source_out"]) - tc_to_sec(s["source_in"])
        except Exception:
            pass
    total += sum(c["duration"] for c in plan["text_cards"])
    plan["duration_seconds"] = round(total, 1)
    return plan


def build_prompt(aud, ctx):
    pkg = ctx.pkg
    cat = [{"moment_id": m.id, "scene": m.scene, "in": sec_to_tc(m.t_in), "out": sec_to_tc(m.t_out), "summary": m.summary, "themes": m.themes}
           for m in pkg.moments.values() if m.id not in ctx.story["blocked_moments"]]
    return ("Plan a trailer.\nAudience: " + aud["name"] + "\nPromise: " + aud["promise"] + "\nBeats: " + str(aud["beats"]) +
            "\nApproved audience signals: " + str([s["id"] + ": " + s["statement"] for s in ctx.signals[aud["id"]]["used"]]) +
            "\nMoment catalog (only use these ids): " + str(cat) +
            "\nReturn JSON: {segments:[{beat,moment_id,reason,signal_refs}], text_cards:[{after_beat,text,claims,type}]}. "
            "Story facts you may cite in card claims: " + str([f["id"] + ": " + f["text"] for f in ctx.story["facts"] if not f["protected"]]))
