# Practical MAIRG POC execution plan

Goal: demonstrate an AI-first application language people can use to build useful software. The user rejected a numeric-only benchmark project, then explicitly requested a proof of concept instead of a full production platform. Human source order/classes/internal names/loops/scheduling are not axioms. Correctness and external semantics remain requirements.

## Phases

1. Practical scope/design: complete; one persistent API/CLI plus independent second app, reusable general graph core.
2. Three new independent reviewers: all APPROVE MAIRG-M4. M3 counterexamples (DB/file alias and response framing) consolidated; revision chain0->1->2->3->4 preserved.
3. Three new builders: language/compiler/runtime; hosts/WSGI; project/CLI/bundle/example. Complete. Root integration/docs/reference comparison.
4. Three new testers: semantics/effects; real DB/server/lifecycle; independently authored different app from public docs with no runtime edits.
5. Practical gates, outside-checkout artifact, same-app Python comparison, public POC publication and CI.

## Checkpoint

Current goal: package/publish the validated practical POC and complete whole-app comparison/CI.
Last verified evidence: all three independent testers PASS;71-test local suite passes; graph inventory and fresh changelog app work outside checkout; clean wheel supports new/check/test/run/build; Python functional comparison smoke matches15 HTTP cases, rollback and restart.
Approach: disjoint builder ownership; no domain logic in runtime; remove old numeric contract instead of compatibility support.
Blocker:none. Consecutive attempts without progress:0.

## Applicable gates

New public format/removal, effects, SQLite persistence, server lifecycle, artifact publication, and review trigger model/temporal gates. Retained examples and absence of released app binaries documented in MODEL_CLOSURE.md. Local DB schema/migration/live-state checks apply to the example. No production deployment or UI/mobile/payment/account work.

Practical independent programmability is a blocking POC gate. Kernel speed is not a substitute. Full package ecosystem/native optimization/production readiness are deferred, explicitly.
