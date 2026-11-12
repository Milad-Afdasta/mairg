# Validation evidence

Date: 2026-11-12. Initial language/toolchain version: 0.1.0. Approved model: MAIRG-M2.

## Local gates

| Gate | Result |
|---|---|
| Three independent specification reviewers | All APPROVE MAIRG-M2 before runtime edits |
| Three new builders | Complete in disjoint ownership lanes |
| Three new independent test/debug agents | All TEST_RESULT PASS |
| Full durable suite | **55 tests passed**, 23.610 seconds on the final masked lowering |
| Package type checks | mypy: no issues in all eight package modules |
| Lint | Ruff passes for package, tests, benchmarks, examples |
| Python syntax compilation | compileall passes for package, tests, benchmarks, examples |
| Native sanitizer execution | Actual emitted C passes AddressSanitizer and UndefinedBehaviorSanitizer |
| Wheel/source distribution build | Both built successfully |
| Clean wheel install | New isolated environment, outside checkout, actual wheel import confirmed |
| Installed CLI | Native and reference engines both produce exact expected telemetry output |
| Native failure/cleanup | Missing/nonzero/load/launch/timeout/cancellation cases pass; owned compiler descendants stopped |
| Public GitHub CI | Configured for Linux/macOS and Python 3.11/3.14; checked after publication |
| Official benchmark | Frozen-source run recorded separately in COMPARISON.md and raw JSON reports |

The tested wheel is `/tmp/mairg-final-package/mairg-0.1.0-py3-none-any.whl`, SHA-256 `2e7e8bf3597bf6659ab37c9994e20ae8c78a345d6f723a6f7d7352b02952e96a`. Temporary wheel-install environments and native builds were cleaned; source and evidence remain.

## Commands

```sh
python3 -m unittest discover -s tests -v
.venv/bin/mypy mairg
.venv/bin/ruff check mairg tests benchmarks examples
python3 -m compileall -q mairg benchmarks examples tests
uv build --out-dir /tmp/mairg-final-package
```

The isolated-wheel smoke installed the wheel into a fresh environment, removed inherited Python path overrides, changed working directory outside the checkout, verified the imported module came from that environment, and ran the installed `mairg` executable with both engines against examples/expected.json.

## Integration evidence

- The telemetry application produces total_requests=4, errors=2, slow_requests=2, total_bytes=6400, error_bytes=5000, max_latency=5000, weighted_score=27697 on the supplied example.
- Its18-node source optimizes to11 nodes: one constant fold, two merged nodes, three dead nodes. Generated code has one row traversal and no intermediate column arrays.
- All18 scalar operations and four reductions have direct catalog coverage, including bool overloads and unsigned boundaries.
- Independent source/mathematical oracles, optimized/unoptimized interpretation/native code, and independent handwritten C++ agree.
-32 seeded randomized native cases,500 durable canonicalization trials, and another1,000 exploratory ID/order trials passed.
- Invalid unreachable state, excessive depth/nesting, bad final records, forged object metadata, stale patches, schema mismatches, and closed runtimes reject at their owners.
- Native code contains positional identities and compiler-generated names; external names never become code or shell instructions.
- Public compiled execution is synchronous. Trusted raw ctypes integration is explicitly outside the safety boundary. Native sandboxing is not claimed.

## Residual limits and adjacent work

- This is a deliberately bounded `u64`/`bool` batch-worker language, not a general-purpose app platform.
- JSON/opcode authoring efficiency, model repair success, and token cost remain unmeasured; controlled held-out model trials are the next product experiment.
- A measured gain over one C++ implementation does not imply an inherent language advantage; the primary native comparison includes hand-tuned C++.
- Local performance is hardware/compiler/workload-specific. Cold end-to-end runs include the common Python host and compilation. Whole-process RSS is not per-kernel allocation.
- The native compiler is trusted, concurrent close/use is unsupported, SIGKILL cleanup cannot be guaranteed, and OS library mappings may outlive context cleanup.
- No UI, database, mobile runtime, credentials, payments, or production service was changed. Their gates do not apply.
- Instruction improvement classification: execution miss for the design/implementation assumptions identified during review/testing. Existing rules already required the missing reasoning. Enforcement is in owner code, invariant tests, independent review, and benchmark evidence; no instruction edit was warranted. See INSTRUCTION_REVIEW.md.
