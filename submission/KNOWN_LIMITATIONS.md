# Known limitations

* **Synthetic data.** The `dialect_a` / `dialect_b` subtitle tracks in the sample are English paraphrases that stand
  in for real dialect translations, and cultural context is invented. A native-speaker reviewer is mandatory before
  any dialect trailer is used.
* **No pixel/audio analysis.** "Multimodal grounding" uses supplied scene descriptions, ratings, cues and stems
  metadata. A vision model would be needed to catch content that is missing from the annotations (an unannotated
  face, logo or gesture). The assumption "dialogue and music stems are separable" is surfaced as an approval.
* **Spoiler detection is rule + pattern based.** It catches annotated reveals, keyword patterns in dialogue, protected
  scenes and reveal statements in text. It can miss *inferential* spoilers (a clip that only hints at a twist through
  visuals or ordering). Ordering-based spoilers are only covered through the final-act-zone warning.
* **Impact analysis can be over-inclusive.** Policy/contract evidence tokens are attached to every segment, so a
  policy change such as `changes/family_policy_stricter.json` marks all family segments "affected" even when nothing
  needed to change (the re-verification then makes zero edits). Contract and signal changes are selective.
* **Replay planner outputs are recorded**, so the sample run shows the verifier/repair behaviour, not fresh model
  creativity. The `live` adapter (`llm.py`) is a thin optional wrapper and has **not been exercised** here (no API key).
* **Costs are simulated** in replay/heuristic mode; live numbers are estimates from `cost_sheet.json`.
* **Repairs are local.** The loop swaps flags/footage but does not re-write the audience promise; a plan that needs a
  different story angle ends `REJECTED` for a human editor.
* Bias filter uses a simple rule (behavioural basis, sample >= 500, no stereotype wording). It does not test for
  subtler confounding.

## Decisions that must stay with humans
| decision | owner |
|---|---|
| Final cut, pacing, and whether a `PASS_WITH_WARNINGS` trailer ships | Editorial |
| Music licence renewal near campaign end; any licence needing sign-off (`approval_legal_*`) | Legal |
| Actor conditions (minors, voice/likeness territories), any contract dispute | Legal |
| Dialect translation, tone and audience promise for the dialect audience; traditional music | Cultural reviewer (native speaker) |
| Whether to accept a marketing request the verifier rejected | Editorial + Marketing lead |
| Confirming audio stems are separable and ratings where human vs AI ratings disagree | Audio engineer / Standards |
| Approving which audience signals may be used, and re-auditing region-derived data | Data + Cultural review |
