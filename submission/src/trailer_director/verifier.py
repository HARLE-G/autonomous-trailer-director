"""Independent verifier.

Design rule: this module NEVER imports the planner and never trusts planner-supplied metadata
(tags, reasons, evidence, signal claims). It re-derives facts from the raw package, story map and
constraint map. It can (and in the sample run, does) reject the creative plan.

Each finding: {severity: FAIL|WARN, code, message, segment, card, evidence[]}.
"""
import re
from datetime import date

from .constraints import limits_for, text_bans_for, duration_for, access_for
from .package import DIMS
from .text_rules import kin_terms, word_hits, regex_hits, shouting, norm
from .timecode import tc_to_sec, TimecodeError, overlap

CHECKS = ["source_accuracy", "spoiler", "story_truth", "policy", "rights", "accessibility", "timing", "bias_and_dialect"]


def verify_plan(plan, ctx):
    pkg, story, cmap = ctx.pkg, ctx.story, ctx.cmap
    lex = pkg.lexicons
    aud_id, terr = plan["audience"], plan["territories"]
    aud = pkg.audiences[aud_id]
    F = []

    def add(sev, code, msg, seg=None, card=None, ev=None, check=None):
        F.append({"severity": sev, "code": code, "message": msg, "segment": seg, "card": card,
                  "evidence": ev or [], "check": check or _check_of(code)})

    limits = limits_for(cmap, aud_id, terr)
    bans = text_bans_for(cmap, aud_id, terr)
    dur_rule, acc = duration_for(cmap, aud_id, terr), access_for(cmap)
    actor_rules = {r["subject"]: r for r in cmap["rules"] if r["type"] == "actor_rights"}
    music_rules = {r["subject"]: r for r in cmap["rules"] if r["type"] == "music_rights"}
    allowed_sig = set(ctx.allowed_signal_ids(aud_id))
    camp_end = cmap["campaign"]["end"]
    allowed_kin = set(story["allowed_kinship"])
    fact_by = {f["id"]: f for f in story["facts"]}
    zone = story["zone_start"]
    music_secs = {}

    # ---------------- text scanner ----------------
    def scan(text, where, seg=None, card=None, kind="card", claims=None, card_type="claim"):
        for sp in story["spoilers"]:
            if regex_hits(text, sp["patterns"]):
                add("FAIL", "spoiler_text", f"{where} reveals protected fact {sp['id']} ('{sp['fact']}')", seg, card, [f"spoiler:{sp['id']}"])
        if kind == "subtitle":
            return
        for w in word_hits(text, lex["profanity"]):
            add("FAIL", "policy_profanity_text", f"{where} contains profanity '{w}'", seg, card)
        if regex_hits(text, lex["clickbait"]) or (card_type != "title" and shouting(text)):
            add("FAIL", "clickbait_misrepresentation", f"{where} uses clickbait/shouting framing that misrepresents the story", seg, card)
        for w in word_hits(text, lex["stereotype"]):
            add("FAIL", "stereotype_language", f"{where} uses stereotyping language '{w}'", seg, card, ["policy:cultural-respect"])
        extra = kin_terms(text, lex["kinship_terms"]) - allowed_kin
        if extra:
            add("FAIL", "unsupported_relationship", f"{where} asserts relationship(s) {sorted(extra)} not supported by the episode", seg, card, ["story_map:relationships"])
        if word_hits(text, lex["threat_words"]) and "F06" not in (claims or []):
            add("FAIL", "unsupported_threat", f"{where} implies a threat not backed by claim F06", seg, card)
        for r in bans:
            hits = word_hits(text, r["terms"])
            if hits:
                add("FAIL", "policy_text_ban", f"{where} contains terms {hits} banned by {r['policy']}", seg, card, [r["id"]])

    # ---------------- segments ----------------
    segs = plan.get("segments", [])
    if not segs:
        add("FAIL", "empty_plan", "plan has no segments")
    total = 0.0
    used_ranges = []
    for i, s in enumerate(segs):
        try:
            a, b = tc_to_sec(s["source_in"]), tc_to_sec(s["source_out"])
        except (TimecodeError, KeyError) as e:
            add("FAIL", "timecode_invalid", str(e), i); continue
        if b <= a:
            add("FAIL", "timecode_order", f"source_out {s['source_out']} is not after source_in {s['source_in']}", i); continue
        dur = b - a
        total += dur
        scene = pkg.scenes.get(s.get("video"))
        bad = False
        if scene is None:
            add("FAIL", "source_scene_missing", f"scene '{s.get('video')}' does not exist in the episode package", i, ev=[f"scene:{s.get('video')}"]); bad = True
        if b > pkg.duration + 1e-6:
            add("FAIL", "source_beyond_episode", f"{s['source_out']} is beyond episode end ({plan.get('_episode_end', pkg.duration):.3f}s)", i); bad = True
        if bad:
            continue
        if a < scene.t_in - 1e-6 or b > scene.t_out + 1e-6:
            add("FAIL", "source_outside_scene", f"range is not inside scene {scene.id}", i, ev=[f"scene:{scene.id}"]); continue
        for (ra, rb, j) in used_ranges:
            if overlap(a, b, ra, rb) > 0.5:
                add("WARN", "reused_footage", f"overlaps footage already used in segment {j}", i)
        used_ranges.append((a, b, i))
        moms = pkg.moments_overlapping(scene.id, a, b)
        ev = [f"scene:{scene.id}"] + [f"moment:{m.id}" for m in moms]

        # -- policy (content per moment, conservative)
        content = {d: max([pkg.moment_content(m)[d] for m in moms] or [scene.effective_content[d]]) for d in DIMS}
        for d in DIMS:
            if content[d] > limits[d]["max"]:
                add("FAIL", f"policy_{d}", f"{d} level {content[d]} exceeds limit {limits[d]['max']} for {aud_id} in {terr}", i, ev=limits[d]["rules"] + ev)

        # -- spoilers (annotation, protected scene, dialogue scan, zone)
        for m in moms:
            if m.id in story["blocked_moments"]:
                add("FAIL", "spoiler_moment", f"moment {m.id} is a protected reveal", i, ev=ev + ["spoiler:blocked_moment"])
        if scene.id in story["protected_scenes"]:
            add("FAIL", "spoiler_protected_scene", f"scene {scene.id} is fully protected (ending)", i, ev=ev)
        cues = pkg.cues_overlapping(scene.id, a, b)
        audio = s.get("audio", {})
        for c in cues:
            scan(c.text, f"dialogue {c.id}", i, kind="subtitle")
        if b > zone and not any(f["code"].startswith("spoiler") and f["segment"] == i for f in F):
            add("WARN", "spoiler_zone_adjacent", "footage is in the protected final-act zone; editorial approval required", i, ev=ev)

        # -- subtitles / dialogue truth / accessibility
        track = s.get("subtitle_track", "source")
        sub = norm(s.get("subtitle", ""))
        if audio.get("dialogue") and cues:
            texts = [pkg.cue_text(c, track) for c in cues]
            if any(t is None for t in texts):
                add("FAIL", "subtitle_track_missing_cue", f"track '{track}' has no text for cue(s) {[c.id for c in cues if pkg.cue_text(c, track) is None]}", i)
            else:
                expected = norm(" ".join(texts))
                if sub != expected:
                    add("FAIL", "subtitle_not_in_source", "subtitle does not match the supplied track for these cues (possible invented quote)", i, ev=[f"cue:{c.id}" for c in cues])
                for c in cues:
                    src_k, sub_k = kin_terms(c.text, lex["kinship_terms"]), kin_terms(pkg.cue_text(c, track), lex["kinship_terms"])
                    if src_k != sub_k:
                        add("FAIL", "subtitle_relationship_mismatch", f"track '{track}' cue {c.id} changes relationship terms {sorted(src_k)} -> {sorted(sub_k)}", i,
                            ev=[f"cue:{c.id}", f"track:{track}"], check="story_truth")
                    if c.t_in < a - 1e-3 or c.t_out > b + 1e-3:
                        add("WARN", "cue_cut_mid_sentence", f"cue {c.id} is cut by the segment boundary", i)
                cps = len(expected) / dur
                if cps > acc["max_cps_fail"]:
                    add("FAIL", "subtitle_too_fast", f"{cps:.1f} chars/s exceeds {acc['max_cps_fail']}", i, ev=[acc["id"]])
                elif cps > acc["max_cps_warn"]:
                    add("WARN", "subtitle_fast", f"{cps:.1f} chars/s is above comfortable {acc['max_cps_warn']}", i, ev=[acc["id"]])
        elif not cues and sub:
            add("FAIL", "subtitle_without_dialogue", "subtitle text supplied but no dialogue exists in this range (invented quote)", i)
        if track != aud["subtitle_track"] and cues and audio.get("dialogue") and "subtitle_relationship_mismatch" not in [f["code"] for f in F if f["segment"] == i]:
            add("WARN", "subtitle_track_differs_from_audience", f"uses '{track}' instead of audience track '{aud['subtitle_track']}'", i)
        if track != aud["subtitle_track"] and cues and audio.get("dialogue") and aud["dialect"]:
            add("WARN", "dialect_track_fallback", f"dialect audience served '{track}' subtitles for cue(s); cultural reviewer must confirm", i, check="bias_and_dialect")

        # -- voice-over
        vo = audio.get("voice_over")
        if vo:
            scan(vo.get("text", ""), "voice-over", i, kind="vo")
            if not vo.get("on_screen_text"):
                add("FAIL", "audio_only_information", "voice-over has no on-screen text equivalent", i)
            if str(vo.get("voice", "")).startswith("clone:"):
                who = vo["voice"].split(":", 1)[1]
                rule = actor_rules.get(f"actor_{who}")
                if not rule or not rule["voice_clone_allowed"]:
                    add("FAIL", "actor_voice_clone_denied", f"voice cloning of {who} is not permitted by contract", i, ev=[rule["contract"]] if rule else [])

        # -- rights: actors
        chars = set().union(*[set(m.characters) for m in moms]) if moms else set(sum((pkg.moments[x].characters for x in scene.moment_ids), []))
        for ch in sorted(chars):
            actor = pkg.actor_of(ch)
            r = actor_rules.get(actor)
            if not r:
                add("FAIL", "actor_no_contract", f"no contract found for {ch} ({actor})", i); continue
            cev = [r["contract"], r["id"]]
            if not r["promo_allowed"]:
                add("FAIL", "actor_promo_denied", f"{ch}: promotional use not permitted", i, ev=cev)
            miss = set(terr) - set(r["territories"])
            if miss:
                add("FAIL", "actor_territory_denied", f"{ch} ({actor}) not cleared for {sorted(miss)}" + (f": {r['note']}" if r.get("note") else ""), i, ev=cev)
            if r["expires"] < camp_end:
                add("FAIL", "actor_contract_expired", f"{ch}: contract expires {r['expires']} before campaign end {camp_end}", i, ev=cev)
            for cond in r["conditions"]:
                ic = cond["if_content"]
                if content[ic["dim"]] >= ic["gte"]:
                    add("FAIL", f"actor_condition_{cond['code']}", f"{ch}: {cond['note']}", i, ev=cev)

        # -- rights: music (incl. music baked under source dialogue)
        tracks = set()
        if audio.get("source_music") and scene.music:
            tracks.add(scene.music)
        if audio.get("music_override"):
            tracks.add(audio["music_override"])
        for t in sorted(tracks):
            r = music_rules.get(t)
            if not r:
                add("FAIL", "music_no_license", f"track {t} has no licence record", i); continue
            cev = [r["contract"], r["id"]]
            if not r["promo_allowed"]:
                add("FAIL", "music_promo_denied", f"track {t}: promotional use not permitted", i, ev=cev)
            miss = set(terr) - set(r["territories"])
            if miss:
                add("FAIL", "music_territory_denied", f"track {t} not licensed for {sorted(miss)}", i, ev=cev)
            if r["expires"] < camp_end:
                add("FAIL", "music_license_expired", f"track {t} licence expires {r['expires']} before campaign end {camp_end}", i, ev=cev)
            elif (date.fromisoformat(r["expires"]) - date.fromisoformat(camp_end)).days < 300:
                add("WARN", "music_license_expiry_near", f"track {t} licence ends {r['expires']}, close to campaign end; legal must confirm renewal", i, ev=cev)
            music_secs[t] = music_secs.get(t, 0) + dur
            if r["max_total_seconds"] and music_secs[t] > r["max_total_seconds"] + 1e-6:
                add("FAIL", "music_cap_exceeded", f"track {t} used {music_secs[t]:.1f}s > cap {r['max_total_seconds']}s", i, ev=cev)
            if r.get("approval"):
                add("WARN", f"approval_{r['approval']}_{t}", f"track {t} requires {r['approval']} approval", i, ev=cev)

        # -- bias: only behaviour-based, sufficiently sampled signals may justify personalization
        for sg in s.get("signal_refs", []):
            if sg not in allowed_sig:
                basis = pkg.signals.get(sg, {}).get("basis", "unknown")
                add("FAIL", "unapproved_signal", f"signal {sg} (basis: {basis}) is not approved for personalization", i, ev=[f"signal:{sg}"])

    # ---------------- text cards ----------------
    cards = plan.get("text_cards", [])
    beats = {s.get("beat") for s in segs} | {"end", "start"}
    card_total = 0.0
    for j, c in enumerate(cards):
        txt, claims, ctype = c.get("text", ""), c.get("claims", []), c.get("type", "claim")
        card_total += c.get("duration", 0)
        scan(txt, f"text card {j}", card=j, claims=claims, card_type=ctype)
        if ctype != "title":
            if not claims:
                add("FAIL", "unreferenced_claim", "text card makes a story claim but cites no supported fact", card=j)
            for cid in claims:
                f = fact_by.get(cid)
                if f is None:
                    add("FAIL", "unsupported_claim", f"claim {cid} does not exist in the story map", card=j)
                elif f["protected"]:
                    add("FAIL", "spoiler_claim", f"claim {cid} is a protected fact", card=j, ev=[f"spoiler:{cid}"])
                elif not f["verified"]:
                    add("FAIL", "unverified_claim", f"claim {cid} has no dialogue evidence", card=j)
        need = max(acc["min_card_seconds"], len(txt) / acc["card_cps"])
        if c.get("duration", 0) + 1e-6 < need:
            add("FAIL", "text_card_too_fast", f"card shown {c.get('duration', 0)}s, needs >= {need:.1f}s to read", card=j, ev=[acc["id"]])
        if c.get("after_beat") not in beats:
            add("FAIL", "card_anchor_missing", f"anchor beat '{c.get('after_beat')}' not present", card=j)
    scan(plan.get("audience_promise", ""), "audience promise")

    # ---------------- plan level: timing, dialect treatment ----------------
    tot = round(total + card_total, 1)
    if abs(tot - plan.get("duration_seconds", tot)) > 0.6:
        add("WARN", "duration_declared_mismatch", f"declared {plan.get('duration_seconds')}s but timeline is {tot}s")
    if tot < dur_rule["min"] or tot > dur_rule["max"]:
        add("FAIL", "duration_out_of_range", f"{tot}s outside allowed {dur_rule['min']}-{dur_rule['max']}s", ev=dur_rule["rules"])
    for i, s in enumerate(segs):
        try:
            if tc_to_sec(s["source_out"]) - tc_to_sec(s["source_in"]) < 1.0:
                add("FAIL", "segment_too_short", "segment under 1.0s is unreadable/unwatchable", i)
        except Exception:
            pass
    if aud["dialect"]:
        sub_segs = [s for s in segs if s.get("subtitle") and s.get("subtitle_track") == aud["subtitle_track"]]
        comedic = [s for s in sub_segs if pkg.moments.get(s.get("moment_id", ""), None) and pkg.moments[s["moment_id"]].themes == ["humour"]]
        if sub_segs and len(comedic) / len(sub_segs) >= 0.5:
            add("WARN", "dialect_as_comic_device", "half or more of the dialect-subtitled segments are purely comic")
        if not any("culture" in _themes(pkg, s) for s in segs):
            add("WARN", "no_cultural_grounding", "dialect trailer has no segment grounded in cultural context")
        if not any(s.get("signal_refs") for s in segs):
            add("WARN", "no_behavioural_evidence", "no segment cites an approved behavioural signal; personalization is unsupported")
    order = {"FAIL": 0, "WARN": 1}
    F.sort(key=lambda f: (order[f["severity"]], f["segment"] if f["segment"] is not None else -1, f["code"]))
    status = "REJECTED" if any(f["severity"] == "FAIL" for f in F) else ("PASS_WITH_WARNINGS" if F else "PASS")
    return {"status": status, "findings": F, "timeline_seconds": tot, "checks_run": CHECKS}


def _themes(pkg, s):
    a, b = tc_to_sec(s["source_in"]), tc_to_sec(s["source_out"])
    sc = pkg.scenes.get(s.get("video"))
    return {t for m in pkg.moments_overlapping(sc.id, a, b) for t in m.themes} if sc else set()


def _check_of(code):
    if code.startswith("spoiler"): return "spoiler"
    if code.startswith("policy") or code.startswith("text_ban"): return "policy"
    if code.startswith(("actor", "music")): return "rights"
    if code.startswith(("source", "timecode")): return "source_accuracy"
    if code.startswith(("subtitle", "text_card", "audio_only", "cue")): return "accessibility"
    if code.startswith(("duration", "segment", "reused", "empty", "card_anchor")): return "timing"
    if code.startswith(("unapproved", "stereotype", "dialect", "no_", "approval")): return "bias_and_dialect"
    return "story_truth"
