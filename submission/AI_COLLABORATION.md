# AI collaboration note

> **Author to complete before submitting.** The interview asks "What did Claude/Codex suggest that looked plausible
> but was wrong?". Only you can answer that truthfully, so the sections marked **[Prompts and iteration

I used AI assistance primarily for architecture exploration, implementation scaffolding, test generation, and documentation. My instructions emphasized a verification-first design: the planner should propose trailer decisions, while an independent verifier should be able to reject those decisions based on episode evidence, policy rules, rights constraints, and spoiler checks.

After reviewing the generated output, I refined the implementation to avoid trusting planner-provided metadata and ensured that validation was performed against the underlying episode and constraint data.

Plausible but incorrect AI suggestion

One generated suggestion was to rely on planner-provided scene metadata and evidence when validating a trailer segment. This looked reasonable because the planner already had access to the same information, but it would allow an incorrect or hallucinated planner decision to pass validation.

I changed the implementation so that the verifier independently checks scene existence, applicable constraints, and finding codes against the source data rather than trusting the planner's claims.

Manual changes

I manually reviewed the generated implementation, corrected test assumptions where they did not match the source data, and refined the documentation to describe only capabilities that are actually implemented.

I also kept the multimodal limitation explicit: the current implementation does not perform direct pixel-level video analysis, so it does not claim full visual multimodal verification.]** are left for you.
> Everything else below is limited to what has been checked and can be reproduced.

## How AI was used
The project (data generator, pipeline, verifier, repair loop, tests, docs) was produced with AI assistance.
Direction given to the assistant, in order: (1) design the assignment as *verification-first* - a verifier that can
reject the planner; (2) build synthetic data with traps taken from the brief's "surprise events"; (3) implement
policies/contracts as rules; (4) tests for each required scenario; (5) documentation from the code as it exists.

## How output was checked (reproducible)
* `python -m pytest -q` -> 31 passed.
* `python -m trailer_director run --out /tmp/x` regenerates `sample_run/` **byte-for-byte identical**
  (`diff -rq sample_run /tmp/x`), i.e. the committed sample is not hand-edited.
* All surprise scenarios were executed, not just described: outage, budget guard, heuristic mode, and each file in
  `changes/`. Observed: `music_expiry` touched only dialect-region (4 segments), `subtitle_fix` touched one segment in
  each of two trailers, the others were untouched.
* Docs were written after reading the modules, and numbers quoted in them (31 tests; 5/15/8 first-draft FAILs; final
  statuses) were read from actual runs.

## A concrete case where AI-generated output was wrong (found and fixed)
`tests/test_required.py::test_music_cap_is_enforced` failed. The test asserted that five moments exceed the 30 s play
cap of track M-02, but their real total is 29.4 s (4.4 + 6.0 + 6.0 + 6.5 + 6.5), so the verifier was *correct* and the
test arithmetic was wrong. The fix was to the test (add the 6.0 s moment `s10b`, total 35.4 s), not to the verifier.
Lesson: when a generated test disagrees with a generated checker, compute the ground truth from the data before
deciding which one to change.

## Other risks to watch when using AI on this task
* Tests that pass trivially (asserting a status, not the specific finding code). Tests here assert finding codes.
* A verifier that trusts planner metadata (tags, reasons, evidence). `verifier.py` is written not to.
* Over-claiming: e.g. calling the pipeline "multimodal" though it does no pixel analysis (see KNOWN_LIMITATIONS).
* Impact analysis that looks selective but isn't (policy tokens on every segment).

## [Prompts and iteration

I used AI assistance primarily for architecture exploration, implementation scaffolding, test generation, and documentation. My instructions emphasized a verification-first design: the planner should propose trailer decisions, while an independent verifier should be able to reject those decisions based on episode evidence, policy rules, rights constraints, and spoiler checks.

After reviewing the generated output, I refined the implementation to avoid trusting planner-provided metadata and ensured that validation was performed against the underlying episode and constraint data.

Plausible but incorrect AI suggestion

One generated suggestion was to rely on planner-provided scene metadata and evidence when validating a trailer segment. This looked reasonable because the planner already had access to the same information, but it would allow an incorrect or hallucinated planner decision to pass validation.

I changed the implementation so that the verifier independently checks scene existence, applicable constraints, and finding codes against the source data rather than trusting the planner's claims.

Manual changes

I manually reviewed the generated implementation, corrected test assumptions where they did not match the source data, and refined the documentation to describe only capabilities that are actually implemented.

I also kept the multimodal limitation explicit: the current implementation does not perform direct pixel-level video analysis, so it does not claim full visual multimodal verification.] items to add from your own sessions
- Prompts you gave and what you changed after reviewing the output.
- 1-2 suggestions from Claude/Codex that looked plausible but were wrong, and how you caught them.
- Anything you rewrote by hand.
