# Practical POC validation

Date 2026-11-12. Tool version 0.2.0. Current approved contract: MAIRG-M4. This is an experimental application POC, not production readiness.

| Gate | Result |
|---|---|
| Three new specification reviewers | All APPROVE MAIRG-M4 before runtime edits |
| Three new builders | Completed disjoint language, hosts, and tooling/application lanes |
| Three new independent test/author agents | All PASS; 15 semantics, 8 persistence, 5 fresh-app tests |
| Full local suite | 71 tests passed in 10.418s including packaging notice checks |
| Type/lint/syntax | mypy passes nine package modules; Ruff/compileall pass |
| Real persistent application | Graph-defined inventory CRUD and atomic reservations; HTTP/CLI |
| DB/migration/master | Actual local schema/rows checked; version1 retained data/restart; wrong/newer schema refused |
| Transaction/concurrency | Later failure rolls back both writes; simultaneous reservations cannot oversell |
| Independent application | Graph-only release-note generator authored from public docs/tools, no runtime reading/edit |
| Clean tool wheel | New/check/test/run/build passed outside checkout after wheel installation |
| Clean application execution | Bare interpreter with no MAIRG runs generated artifact under -I |
| Artifact content | Compiler/source fallback absent; runtime/Waitress notices and integrity metadata included |
| Whole-app Python comparison | 15 HTTP cases, rollback and restart pass; measured MAIRG median2.174ms vs Python0.535ms; [full report](COMPARISON.md) |
| GitHub CI | [POC matrix](https://github.com/Milad-Afdasta/mairg/actions/runs/43267711309) PASS at bbdfc76: Linux/macOS, Python3.11/3.14; all four jobs pass unit tests, type/lint/syntax checks and both app fixtures. Old numeric CI is not reused |

Tested wheel: `/tmp/mairg-poc-package/mairg-0.2.0-py3-none-any.whl`, SHA-256 `46edea8932b74b2101033eae84443811cdcdfad8be0a51090801d017ed3c0438`.

The initial practical CI run at003d949 failed because one test helper assumed a local `.venv` interpreter path. No product failure was demonstrated. The helper now uses `sys.executable`; all eight affected tests pass locally and the corrected four-job matrix passes. The final documentation/results commit does not modify runtime, applications, tests, packaging, comparison code, or workflow relative to passing bbdfc76.

Local convenience bundles are available in `artifacts/inventory` and `artifacts/changelog` (ignored generated output). A bare Python interpreter executed the inventory init/create/reserve/list flow: stock10 became8 after a quantity2 reservation. The changelog bundle produced Markdown from two CSV entries under its own external state directory. These demos supplement the automated outside-checkout and failure-path tests.

## Commands

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/mypy mairg
.venv/bin/ruff check mairg tests examples comparison
.venv/bin/python -m compileall -q mairg examples comparison tests
.venv/bin/python -m mairg test examples/inventory
.venv/bin/python -m mairg test examples/changelog
uv build --out-dir /tmp/mairg-poc-package
```

## Practical and failure evidence

Inventory source owns its schema, SQL, validation, statuses, startup checks and transaction boundaries. No table/route/domain operations exist in runtime dispatch. `schema.json` and `migrations/001.json` produce equal actual SQLite schema. Runtime DB state is under explicit STATE_DIR/db; generic files are under disjoint STATE_DIR/files. No initialized user database is packaged.

Independent tests inspect real SQLite after successful/failed/concurrent calls, restart source and built services, and reject unsupported schemas before socket readiness. Framing tests perform a committed write followed by invalid response construction and prove the commit remains exactly once; disconnect never replays a handler. HTTP response headers and UTF-8 length are transport-owned.

Release-note generation proves independently authored non-DB software: quoted/multiline/Unicode CSV; normalization; explicit category/issue/title/version checks; stable grouping/duplicates; Markdown escaping; single atomic publication. Bad later rows preserve previous bytes. Source changes and rebuild alter the result without runtime changes.

## Limits

The current Python-hosted graph runtime is not expected to be faster merely because authoring uses graphs. Report actual whole-app overhead. AI token efficiency, generation/repair success and maintenance advantages remain unmeasured. Multi-module imports, arbitrary libraries, outgoing HTTP, production/security/deployment frameworks and native code optimization are deferred.

There is no sandbox, exactly-once effect guarantee across crashes, global rollback, forced Python-thread termination, or guaranteed rollback after unknown commit outcomes. These limits are explicit, not hidden retries/fallbacks. This work changed no real user/production database, account/payment, UI, or mobile path.

Instruction improvement classification is execution miss: prior scope validation, public metadata snapshotting, artifact notices, and test-interpreter identity were required by existing owner/falsification instructions. Practical independent-app gates, invariant regressions, artifact inspection, and the cross-platform CI matrix enforce them; no instruction edit was warranted. See [instruction review](INSTRUCTION_REVIEW.md).
