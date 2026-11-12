# Specification review evidence

Three new independent reviewers received the same MODEL_CLOSURE.md and SPEC.md. Initial window: 8 minutes; correction checks: 4 minutes each. Reviewers did not edit.

| Revision | Semantics/identity | Native/lifecycle/security | Product/evaluation |
|---|---|---|---|
| MAIRG-M0 | COUNTEREXAMPLE: frozen constructors bypass validator | COUNTEREXAMPLE: constructor provenance and surviving compiler descendants | APPROVE |
| MAIRG-M1 | COUNTEREXAMPLE: serialization drops Node.type/count.value | APPROVE | APPROVE |
| MAIRG-M2 | APPROVE MAIRG-M2 | APPROVE MAIRG-M2 | APPROVE MAIRG-M2 |

M0 findings consolidated into sole-owner validation at every public consumer, exact canonical JSON bytes, process-group compiler ownership, explicit nesting rejection, and accurate evaluation labels. M1 finding consolidated into comparison of original nonserialized metadata with the validated reconstruction, rejecting mismatches. All earlier approvals were superseded by each material revision. Runtime code began only after all three M2 approvals.

Semantics reviewer falsified identity, modular arithmetic, batch atomicity, graph rejection, and patch behavior. Native reviewer used actual Python probes for frozen dataclass permissiveness and spawned-child survival after driver timeout. Root verified the full native ABI on actual Clang/ctypes with wraparound and empty input. Evaluation reviewer required the optimized independent C++ baseline, identical host parsing/prepared buffers, exact metadata, raw samples, and no unmeasured AI-productivity claims.

All reviewers explicitly accepted a bounded domain-specific first release. Graph IR, CSE, fusion, and scheduling separation are prior art, not claimed inventions. The user's machine-first goal is carried by graph semantics and compiler-owned execution choices, while AI generation/repair efficiency remains an experimental hypothesis.
