# Architecture note

## Principles
1. **Creative generation and validation are separate code paths.** `verifier.py` never imports the planner and
   ignores planner-supplied tags, reasons, evidence and signal claims; it re-derives everything from the raw
   package, the story map and the constraint map. It returns `FAIL`/`WARN` findings with evidence references.
2. **Policies and contracts are data, not prompt text.** They are compiled into rules (`constraints.py`) that the
   verifier evaluates per segment/card; the planner prompt only summarises them.
3. **Nothing is forced through.** A repair is accepted only if the verifier re-approves the changed segment.
   If no candidate passes, the failure stays and the trailer status is `REJECTED`.
4. **Model output is a proposal, never evidence.** Unsupported model claims (unknown scenes, unverified facts,
   invented quotes) are rejected by the same checks as any other proposal.

## Planning
For each audience: state the *promise* and emotional journey first (`audiences.json`), then obtain a proposal
(recorded LLM output in replay mode, or `planner.heuristic_proposal`). Segments reference catalogue moments and
beats, so the proposal has a mini-story shape (beats such as hook, setup, mystery, complication, heart, turn, payoff).
Heuristic ranking = 3 x beat/tag match + approved-signal bonus + 0.5 x historic engagement - reuse penalties, so
historic engagement is a *hypothesis with small weight*, not a selection rule. Trailers are planned sequentially and
footage already used elsewhere is penalised; a Jaccard footage-overlap diversity check runs across trailers.

## Memory / state
No hidden memory. All state is explicit artefacts: `story_map.json`, `spoiler_map.json`, `constraint_map.json`,
`bias_report.json`, per-trailer JSON (with `evidence` per segment, `repair_history`, `revision_history`), and the
append-only `decision_log.jsonl`. The change handler re-loads the package + a prior run from these files.

## Multimodal processing
Grounding is done on the supplied annotations: scene/moment timecodes, human and AI descriptions, dialogue cues and
subtitle tracks. Human vs AI content ratings are reconciled conservatively (stricter value wins, mismatch is listed
as an editorial approval). Descriptions are untrusted data: instruction-like sentences are quarantined and
relationship claims without dialogue support are excluded. **No pixel-level vision is run** (see limitations);
the budget model still charges media-analysis minutes so cost planning is realistic.

## Verification (`verifier.py`)
| family | examples of codes |
|---|---|
| source accuracy | `source_scene_missing`, `source_outside_scene`, `source_beyond_episode`, `timecode_order` |
| spoilers | `spoiler_moment`, `spoiler_protected_scene`, `spoiler_text`, `spoiler_claim`, WARN `spoiler_zone_adjacent` |
| story truth | `unsupported_claim`, `unreferenced_claim`, `unsupported_relationship`, `unsupported_threat`, `subtitle_relationship_mismatch`, `subtitle_not_in_source` |
| policy | `policy_<dimension>`, `policy_text_ban`, `clickbait_misrepresentation`, `stereotype_language` |
| rights | `actor_territory_denied`, `actor_condition_*`, `actor_voice_clone_denied`, `music_territory_denied`, `music_license_expired`, `music_cap_exceeded` |
| accessibility / timing | `subtitle_too_fast`, `text_card_too_fast`, `audio_only_information`, `duration_out_of_range`, `segment_too_short` |
| bias / dialect | `unapproved_signal`, WARN `dialect_as_comic_device`, `no_cultural_grounding`, `dialect_track_fallback` |

**Spoiler definition:** a protected fact is revealed by (a) any moment annotated as revealing it, (b) any moment whose
dialogue matches the fact's spoiler patterns (independent keyword scan, catches unannotated reveals), (c) any moment in
a fully protected scene (the ending), or (d) text cards/voice-over that state it. Final-act footage that is not blocked
is allowed only with an editorial-approval warning. The LLM's opinion of what is a spoiler is never the deciding evidence.

## Failure recovery
* **Repair loop** (`repair.py`): least-change order - fix flags on the same footage (strip unapproved signal, swap to
  a licensed music bed, use the audience's subtitle track) -> swap footage -> drop; cards are replaced with verified
  template cards or dropped. Duration is repaired by adding or dropping segments. Every candidate is re-verified.
* **Model outage / budget:** `OutageLLM`, missing replay, or `BudgetExceeded` route to the heuristic planner and log
  the fallback. Each plan carries an estimated live cost and a lower-cost fallback strategy.
* **Marketing requests** are evaluated as if inserted; rejected requests are logged and listed as approvals.

## Change handling (`changes.py`)
Every segment/card stores evidence tokens (`contract:...`, `policy:...`, `cue:...`, `signal:...`, `scene:...`). A change
(contract, policy, subtitle, withdrawn signal, scene rating) is applied to a copy of the package, mapped to tokens,
the plan is re-verified under the new rules, only failing/affected items are repaired, and a check confirms
untouched footage was preserved. Output: revised trailers, `change_report.md`, `change_record.json`, decision log.
Example: `music_expiry.json` revises 4 dialect-region items and leaves the family and young-adult plans untouched.

## Scaling
First to fail: the repair loop (re-verifies the whole plan per candidate, and the variant space is a product of
signal/music/subtitle options) and in-memory catalogue lookups. Fixes: incremental per-segment verification with cached
rule indexes, a candidate pre-filter by static eligibility, and batching model calls.
