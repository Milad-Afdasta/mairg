# Independent test/debug phase

Three NEW agents followed the three builders. They used approved MAIRG-M2 as the test oracle and edited only independent test files. Root consolidated implementation corrections. No test required changing the language ownership model.

## Semantics

TEST_RESULT PASS: seven durable tests cover all 18 scalar operations, four reductions, bool overloads, an independently written numerical oracle, 32 seeded native differential cases with optimized/unoptimized execution, 500 canonical ID/order trials, maximum-depth/repeated edges, unreachable malformed state, forged metadata, stale/atomic patches, strict whole-batch rejection, and pinned input snapshots. Another 1,000 canonicalization trials passed outside the durable suite.

## Native lifecycle and safety

TEST_RESULT PASS: eight durable tests cover forged IR before compiler execution, rejection before native calls, fresh outputs and pinned snapshots, context/launch/cancellation cleanup, SIGTERM-resistant descendant termination, hostile names/compiler-path metacharacters, and actual emitted C executed cleanly under AddressSanitizer and UndefinedBehaviorSanitizer. These tests passed again after the final masked-reduction correction.

## Application and performance

Five independent tests cover all three applications against a separate mathematical oracle, transactional CLI changes and stale rejection, malformed records through both engines, complete benchmark metadata/provenance, and the optional branching baseline including its end-to-end child propagation. All passed after root applied final lowering and strengthened C++.

The performance agent compared six generic code-generation variants across three workloads/four sizes using identical buffers. Conditional grouping performed poorly on representative large input. Pure boolean operations and unsigned selection masks enabled vectorization without source-authored scheduling. A separately hand-tuned C++ implementation achieved the same opportunity; root strengthened the primary C++ baseline and retained the original branching implementation only as an additional labeled comparator. Final numbers come from the frozen-source benchmark reports, not development tuning runs.

## Correction and scope

The consolidated implementation correction belongs to `native.py`: local accumulators, eager 0/1 boolean lowering, unsigned masked reductions, and final output publication. It preserves the approved scalar/batch/ABI contract. No workload names, benchmark data, or hand-selected graph schedules enter the compiler. No compatibility layer, native sandbox, asynchronous lifecycle, or UI was introduced.

Complete run commands, final suite counts, package checks, benchmark evidence, and residual limits are in VALIDATION.md and COMPARISON.md.
