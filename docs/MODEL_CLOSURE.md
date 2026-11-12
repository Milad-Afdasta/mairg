# MODEL CLOSURE

Artifact ID: **MAIRG-M2**. Model revision: **2**. Status: all three requested reviewers approved exact MAIRG-M2; **MODEL_GATE: PASS** before first runtime edit.

Risk triggers: new public language contract; producer/typechecker/optimizer/native consumer boundaries; compiler subprocess and temporary-artifact lifecycle; structured edits; independent reviews. Revision 2 evidence: M1 serialization omitted Node.type and count.value, so an inconsistent manually constructed object could be silently normalized. checked_program now compares all original nonserialized metadata to the validated reconstruction and rejects mismatches; all M1 approvals are superseded. Revision 1 evidence: M0 reviewers demonstrated that frozen dataclasses admit invalid values, so public consumers must revalidate through ir.py; a compiler-like driver timeout left a spawned child running, so build cleanup must own its process group. Canonical JSON bytes and whole-process measurement labels are now explicit. All M0 approvals are superseded. Root failure class: an underspecified machine-authored program crosses an ownership boundary and executes a different or unvalidated meaning.

The exact specification reviewed with this model is `docs/SPEC.md` in the same revision. Changes to an owner, identity, invariant, scenario, sequence, atomicity, cleanup, or risk boundary require the next model revision/ID and invalidate affected approvals.

## Canonical owners and invariants

| Owner | Invariant |
|---|---|
| ir.py registry/typechecker | One exact contract; public consumers revalidate even manually constructed Program objects; validate all nodes before pruning; no implicit casts, aliases, unknown fields, duplicate JSON keys, missing refs, cycles, or unsupported opcodes |
| optimizer.py | Typed total graph meaning preserved; exact-key CSE, lawful constant folds, dead removal after validation; identity independent of declaration order/opaque IDs |
| patch.py | Source revision is distinct from plan fingerprint; stale/invalid edits return no transformed graph or file mutation |
| interpreter.py boundary | Whole batch exactly matches input schema and strict scalar types; same-length columns; reject before native execution |
| interpreter.py oracle | Independent numerical evaluation of original validated graph, explicit u64 masking |
| native.py build | Only fixed C templates; no names as code, no shell, no stale artifacts; owned subprocess group cleaned on failure; success only after completed compile and successful load |
| native.py runtime | Buffers pinned and schema checked; fresh output; closed owner rejects use; cleanup bounded; synchronous lifecycle |
| cli.py | Host handles transport, not telemetry business logic; no result until full success |
| benchmarks/ | Strong independent C++ baseline; same semantics/inputs/flags; correctness before timing; measured evidence distinguished from hypotheses |

## Scenarios: start -> action -> owner -> consumer/side effect -> failure/cleanup

1. Valid source -> loads -> full type/graph validation -> canonical plan -> native build -> valid batch -> exact outputs; context removes owned directory.
2. Shuffled declarations/renamed opaque IDs -> same validation -> optimizer canonicalizes -> same fingerprint/native behavior; no source-order semantics.
3. Missing/malformed/duplicated/replaced/unknown source, excessive JSON nesting, forged Program objects, including unreachable cycles -> validator rejects -> no optimizer/build/native effects.
4. u64 overflow/underflow, empty/all/none-selected rows, threshold boundaries -> total modular operations and reduction identities -> interpreter/native/C++ agree exactly.
5. Valid source plus dead/duplicated/literal-only graph -> validate all first -> optimizer removes/folds/interns -> one fused traversal, unchanged outputs.
6. Missing/extra/null/wrong-type/unequal columns or invalid later record -> boundary rejects entire invocation -> native not called; no partial aggregates.
7. Current source -> patch with matching source revision -> fresh document edited fully -> complete validation -> return replacement source only. Stale base, overlapping edits, removed live reference, malformed replacement -> reject, original unchanged, no stdout/source mutation.
8. Compiler missing/nonzero/exception/timeout or load failure -> isolated compiler group is terminated/reaped with bounded TERM/KILL cleanup before directory removal -> structured error, no published library. Trusted compiler descendants stay in the group. Cancellation leaves no authoritative public state; SIGKILL cleanup is not guaranteed.
9. Successful compile -> batch prepare pins buffers/schema -> repeat calls produce fresh equal outputs. Different-schema batch, close then run/prepare -> reject. Concurrent close/use and raw ABI misuse are explicitly outside synchronous public contract.
10. Hostile external names/literals -> source validator and fixed positional C template -> names cannot become source or shell instructions; no arbitrary native code capability.
11. Successful native/reference outputs -> independent traditional kernel verifies equality -> interleaved timings and raw metadata -> publish only measured conclusions. Mismatch blocks timing conclusions; slow result is evidence, not hidden.
12. Process failure before/during/after CLI transport -> no durable data writes or network side effects in this release. stdout emitted only after success; process termination during OS write can truncate transport and is not an atomic filesystem publication guarantee.

No account/session/role state, retained database data, external service mutations, retries/replay, distributed ordering, or multi-owner durable commit occurs. Retry may only repeat pure compilation/computation; no automatic retry is needed. No cache fallback or rollback source exists. Patch failure rollback is discarding the fresh in-memory document.

## Direct evidence and dependency semantics

- Actual host probes: Python 3.14.7; Apple Clang 17 targeting arm64 macOS; compiler successfully built a uint64_t shared-library function. Actual ctypes call `(2^64-1)+1` returned 0. Pointer width 8 bytes. No Rust toolchain installed; Python+C removes that dependency.
- LLVM/MLIR docs describe existing typed IR and lowering, so no claim that machine IR/fusion is novel.
- Halide docs describe separate algorithm/schedule representation and native compilation, supporting scoped choice.
- Python ctypes docs describe ABI-compatible calls and explicit native-memory risks. This is a trusted generated-kernel integration, not a sandbox.
- Clang/GCC standard unsigned arithmetic and actual wrap probe support modular semantics; sanitizer tests will verify generated code against selected boundaries.
- Fresh private temporary directory plus checked subprocess completion avoids partial/stale public artifacts; failure paths will be injected in native tests before completion.

## Exact edit/mutation sequence

1. Author planning/spec/model documents only; three NEW reviewers falsify this exact artifact/spec (8-minute bounded batch; M1 and M2 correction reviews bounded to 4 minutes each with the same reviewers).
2. Consolidate model findings; revise ID/revision when material; gather exact approvals. No runtime edits until gate passes.
3. Build canonical immutable IR/validator/optimizer/patch; native/runtime/oracle; CLI/application/benchmarks in disjoint ownership lanes. No compatibility code or legacy migrations exist.
4. Integrate; run baseline smoke and operation checks. Commission three NEW independent testers with this same approved model.
5. Consolidate implementation defects into one correction pass; revise model only for evidenced model defects; rerun affected checks.
6. Run app, native/reference/C++ comparisons, raw benchmark/report generation, package smoke and CI. Commit/push reviewed deliverables to a new GitHub repository; no service deployment.

## Temporal initial-state matrices and blast radius

| Retained shape | Count | Producer output | Consumer verdict |
|---|---:|---|---|
| Existing MAIRG persisted projects | 0; new isolated repository | none | No historical transformation |

| Active version | Frozen parser/release | Current output acceptance |
|---|---|---|
| None | No prior binary or release | Initial contract only |

Initial owner scan: project is empty except these docs. Future op/reduction allowlists originate in ir.py, other dispatch implementations are mechanically tested for full coverage. Invalid graph rejects the whole program; invalid record rejects the whole invocation, never silently drops an item. No paginated aggregate exists.

## Falsification cases

- Put a cycle or malformed node in an unreachable subgraph. Removing it before validation violates owner contract.
- Let native `MAX+1` disagree with reference mask: modular invariant is false.
- Rename/reorder graph IDs and get a different canonical plan: program identity owner is wrong.
- Keep a stale patch base that matches optimized semantics but not source revision: conflated identities would allow it; contract must reject.
- Directly construct an invalid frozen Node/Program and call every public consumer: all must reject through the canonical validator.
- Make a compiler driver time out with an ordinary spawned child: bounded cleanup must stop both.
- Make codegen interpolate an output name containing C/shell syntax: violates fixed-template boundary.
- Have an invalid last row call native or produce partial results: violates whole-batch boundary.
- Compare native output only with Python and claim language speed superiority: violates fair-evaluation owner.

Unresolved assumptions: **none in the proposed bounded contract**. AI generation efficiency and speed advantage are open experimental hypotheses, not correctness assumptions. Platform portability beyond macOS/Linux is not promised. Requested reviewer verdicts: semantics/identity **APPROVE MAIRG-M2**; native/lifecycle/security **APPROVE MAIRG-M2**; evaluation/product-scope **APPROVE MAIRG-M2**. M0: semantics COUNTEREXAMPLE (constructor provenance); native COUNTEREXAMPLE (constructor provenance/process group); evaluation APPROVE (superseded). M1: semantics COUNTEREXAMPLE (nonserialized metadata); native APPROVE and evaluation APPROVE (superseded).
