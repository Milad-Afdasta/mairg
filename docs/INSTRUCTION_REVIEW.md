# Instruction improvement closeout

Applicable chain: the user's supplied standing execution contract and the matching `/Users/null/.codex/AGENTS.md`. The project is isolated under `/Users/null/work/mairg`; no repository/nested instruction file existed. No instruction file was changed.

## Execution miss: validation provenance and metadata

The first model treated frozen typed IR objects as an insufficiently specified validation boundary. The first correction still allowed serialization to omit inconsistent internal metadata. The missed reasoning step was to trace every way a public consumer can receive an object, including manual construction and fields absent from the wire form.

Existing instructions already require: “Respect the source of truth”; “Fail closed when the source of truth cannot provide required identity, geometry, type, or state”; and model falsification against “missing, stale, malformed, duplicated, replaced, or retained state.” The model-closure gate also states that names and wrappers are not proof. The design diverged by not fully specifying the provenance check before reviewers supplied concrete malformed cases.

Enforcement: the sole `checked_program` owner validates source structure and compares nonserialized metadata with the inferred reconstruction. All public consumers reject invalid objects; independent tests exercise forged object paths. All three reviewers approved MAIRG-M2 before product implementation. No instruction edit was warranted.

## Execution miss: compiler descendant cleanup

The first model bounded the driver subprocess but omitted ordinary compiler descendants. The missed reasoning step was to trace side effects and cleanup beyond the immediate process object.

Existing instructions already require tracing producer/consumer/side-effect paths and falsifying failures before, between, and after multi-owner side effects, including cleanup. The initial design stopped at the direct subprocess API rather than checking its actual timeout semantics. A reviewer used a live spawned-child probe to disconfirm that assumption.

Enforcement: builds own an isolated process group, bounded termination escalation, direct-child reaping, and temporary-directory cleanup. Timeout tests exercise a compiler-like driver with a descendant. This is product/lifecycle enforcement, not a reusable instruction gap. No instruction edit was warranted.

## Final audit

The initial native lowering also carried unmeasured assumptions about conditional branches and predicate grouping. Large-workload measurements disproved the small-workload intuition. Existing instructions already require evidence, falsification, performance preservation, and a pivot after a failed assumption: this is an execution miss, not an instruction gap. Enforcement is generic eager boolean/masked-reduction lowering, exact differential/sanitizer tests, representative workloads, and a strengthened independent hand-tuned C++ primary baseline. The original branching C++ is an explicitly labeled additional comparator, never a runtime fallback. No instruction edit was warranted.

Build-time reporting initially compared one compiler-plus-load interval with a compiler-only interval. This is an execution miss: the existing boundary-tracing and evidence requirements already demand equivalent measurement scopes. Compiler process time and total source-to-loaded-kernel time now have separate, explicitly matched timers for both backends. The complete suite was rerun and the official benchmark repeated on the corrected frozen source. No instruction edit was warranted.

Final product verification and any implementation-defect closeouts are recorded in VALIDATION.md after the independent test/debug phase. The discovery of a compiler or runtime bug is not itself evidence that durable instructions need another paragraph. Existing owner-first, falsification, failure, and evidence rules already cover this process class.
