"""Story map + spoiler map builder.

Deterministic 'extractor': every fact must be VERIFIED against dialogue evidence; descriptions are
treated as untrusted data (prompt-injection sentences are quarantined, invented relationships flagged).
A live LLM could enrich this map, but the verifier only trusts entries that carry evidence.
"""
import re

from .text_rules import kin_terms, regex_hits, sentences
from .timecode import sec_to_tc, tc_to_sec, overlap
from .package import DIMS


def build_story_map(pkg, log=None):
    lex, ep = pkg.lexicons, pkg.episode
    kin_map = lex["kinship_terms"]

    # ---- facts, verified against dialogue evidence
    facts = []
    for f in ep["facts"]:
        pool = " ".join(c.text.lower() for c in pkg.cues.values() if c.scene in f["scenes"])
        missing = [t for t in f["evidence_terms"] if t.lower() not in pool]
        facts.append({"id": f["id"], "text": f["text"], "scenes": f["scenes"], "protected": bool(f.get("protected")),
                      "verified": not missing, "unverified_terms": missing,
                      "evidence_cues": [c.id for c in pkg.cues.values() if c.scene in f["scenes"]
                                        and any(t.lower() in c.text.lower() for t in f["evidence_terms"])]})
    fact_by = {f["id"]: f for f in facts}

    # ---- relationships (only those backed by verified facts)
    rels, allowed_kin = [], set()
    for r in ep["relationships"]:
        ok = fact_by.get(r["fact"], {}).get("verified", False)
        rels.append({**r, "verified": ok})
        if ok:
            allowed_kin |= set(r["kin_terms"])
    protected_kin = {k for f in ep["facts"] if f.get("protected") for k in f.get("kinship", [])}

    # ---- untrusted descriptions: quarantine injections, flag invented relationships
    quarantined, unsupported, scene_text = [], [], {}
    for s in pkg.scenes.values():
        for fld, text in (("human_description", s.human_description), ("ai_description", s.ai_description)):
            keep = []
            for sent in sentences(text):
                if regex_hits(sent, lex["injection"]):
                    quarantined.append({"scene": s.id, "field": fld, "text": sent,
                                        "action": "removed from usable evidence; treated as data, never as instruction"})
                    continue
                extra = kin_terms(sent, kin_map) - allowed_kin - protected_kin
                if extra:
                    unsupported.append({"scene": s.id, "field": fld, "text": sent, "unsupported_relations": sorted(extra),
                                        "action": "claim excluded; not supported by dialogue or verified facts"})
                    continue
                keep.append(sent)
            scene_text.setdefault(s.id, {})[fld] = " ".join(keep)

    # ---- description conflicts (human vs AI ratings) -> conservative max used downstream
    conflicts = []
    for s in pkg.scenes.values():
        diff = {d: {"human": s.content[d], "ai": s.ai_content[d]} for d in DIMS if s.content[d] != s.ai_content[d]}
        if diff:
            conflicts.append({"scene": s.id, "dims": diff, "resolution": "stricter value used"})

    # ---- spoiler map: annotation + independent keyword scan of dialogue
    zone = tc_to_sec(ep["spoiler_zone_start"])
    protected_scenes = ep["protected_scenes"]
    spoilers = []
    for f in ep["facts"]:
        if not f.get("protected"):
            continue
        reveals = [{"moment": m.id, "source": "annotation"} for m in pkg.moments.values() if f["id"] in m.reveals]
        seen = {r["moment"] for r in reveals}
        for cue in pkg.cues.values():
            if regex_hits(cue.text, f["spoiler_patterns"]):
                for m in pkg.moments_overlapping(cue.scene, cue.t_in, cue.t_out):
                    if m.id not in seen:
                        reveals.append({"moment": m.id, "source": "keyword_scan", "cue": cue.id})
                        seen.add(m.id)
        spoilers.append({"id": f["id"], "fact": f["text"], "patterns": f["spoiler_patterns"], "reveal_moments": reveals})
    blocked = {r["moment"] for sp in spoilers for r in sp["reveal_moments"]}
    blocked |= {m.id for m in pkg.moments.values() if m.scene in protected_scenes}
    zone_moments = sorted(m.id for m in pkg.moments.values() if m.t_out > zone and m.id not in blocked)

    engagement = sorted(({"scene": sid, "engagement": v, "contains_blocked_moment": any(pkg.moments[m].id in blocked for m in pkg.scenes[sid].moment_ids)}
                         for sid, v in pkg.history.items()), key=lambda x: -x["engagement"])

    sensitive = [{"moment": m.id, "scene": m.scene, "levels": pkg.moment_content(m)} for m in pkg.moments.values()
                 if max(pkg.moment_content(m).values()) >= 2]
    story = {
        "episode_id": ep["episode_id"], "title": ep["title"], "duration": ep["duration"],
        "characters": [{**c, "actor": pkg.cast.get(c["id"]),
                        "scenes": sorted({m.scene for m in pkg.moments.values() if c["id"] in m.characters})} for c in ep["characters"]],
        "relationships": rels, "allowed_kinship": sorted(allowed_kin),
        "facts": facts,
        "major_events": [{"scene": s.id, "start": sec_to_tc(s.t_in), "event": s.beat} for s in pkg.scenes.values()],
        "emotional_turns": [{"scene": s.id, "emotion": s.emotion} for s in pkg.scenes.values()],
        "sensitive_content": sensitive,
        "spoilers": spoilers, "blocked_moments": sorted(blocked), "protected_scenes": protected_scenes,
        "zone_start": zone, "zone_moments": zone_moments,
        "quarantined_instructions": quarantined, "unsupported_description_claims": unsupported,
        "description_conflicts": conflicts, "usable_descriptions": scene_text,
        "historic_engagement_vs_safety": engagement,
    }
    if log:
        log.event("story_map", "built", facts=len(facts), unverified=[f["id"] for f in facts if not f["verified"]],
                  blocked_moments=sorted(blocked), quarantined=len(quarantined), unsupported_claims=len(unsupported),
                  keyword_scan_only=[r["moment"] for sp in spoilers for r in sp["reveal_moments"] if r["source"] == "keyword_scan"])
    return story
