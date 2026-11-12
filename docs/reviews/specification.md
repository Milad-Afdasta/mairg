# Specification review evidence

Current artifact: MAIRG-M4 revision4. Three NEW reviewers read exact MODEL_CLOSURE.md and SPEC.md; no edits. Initial window6minutes; correction3minutes.

| Lens | M3 | M4 |
|---|---|---|
| Semantics/effects | COUNTEREXAMPLE: file host could overwrite DB path, invalidating effect domains | APPROVE MAIRG-M4 |
| SQLite/host/lifecycle | COUNTEREXAMPLE: app framing headers could truncate JSON/assert in Waitress | APPROVE MAIRG-M4 |
| Practical workflow/validation | APPROVE (superseded) | APPROVE MAIRG-M4 |

Correction: disjoint canonical state/files and state/db roots with alias rejection; sole HTTP framing ownership in web.py, forbidden application framing/hop headers, actual UTF-8 byte length and transport header checks. Failure/cleanup guarantees are explicit and do not promise forced Python-thread termination.

Earlier M0-M2 numeric-only approvals were invalidated by the user's practical-use correction. Their runtime was removed; Git history preserves that experiment. No prior approval was reused to authorize application-runtime edits.
