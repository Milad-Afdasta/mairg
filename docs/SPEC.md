# MAIRG practical proof of concept

Status: approved and implemented POC contract for MAIRG-M4, model revision 4. All three new specification reviewers approved the exact model before runtime edits. This supersedes the numeric-only product scope and the larger production-platform draft. The user's latest request is a practical POC. This status update changes no language semantics.

## Success

A new developer can create a project, express application logic as a graph, check/test it, run a useful persistent HTTP API or CLI, build a runnable directory, and run it outside the checkout without installing MAIRG. A fresh agent must author a second different application using the documented language with no runtime edits. Complete-application behavior and a traditional Python comparison follow practical acceptance; numeric microbenchmarks are not success.

This POC is not production-ready or a universal app platform. Multi-module imports, third-party adapters, outgoing HTTP, authentication frameworks, native optimization, and broad deployment integrations are deferred. These are not replaced by hidden Python callbacks containing the application.

## Source and values

The only current program format is `mairg-app-1`; old `mairg-1` is rejected, not converted or dispatched to another engine. Exactly `format`, `functions`, `exports` are present.

Values: null, exact bool, int, finite float, Unicode str, list, string-keyed record. Bool/int/float are distinct for equality. Arithmetic on ints is exact; floats are binary64 and must stay finite. No implicit numeric/string casts. JSON duplicate keys, NaN/Infinity, malformed/excessively nested input, cycles in supplied Python values, and non-JSON values reject with structured errors.

Function type atoms: `any`, `null`, `bool`, `int`, `float`, `number`, `str`, `list`, `record`. Number accepts int/float, never bool. Any is an explicit dynamic JSON type. Params and return values are checked; operations check actual operands at runtime. This POC does not claim complete static type inference.

Functions are unordered objects with exactly `id`, `params`, `result`, `nodes`, `return`. ID is an opaque integer 0..2^31-1, unique in the program. Params is an ordered array of type atoms; result is one atom; return is a node ID. Exports maps meaningful external names to function IDs. All functions, including uncalled ones, validate. Recursion is allowed.

Nodes are unordered objects with required `id`, `op`, `args`, optional `after`, plus only catalog-specific fields. IDs are opaque and unique per function. Args/after are local node references; forward references are valid. After defaults empty, cannot duplicate entries, and adds causal dependencies whose values are discarded. A function graph must be acyclic, including dead nodes. Every node validates before pure dead-node removal. Every effectful node must be reachable from return through args/after; unused writes/calls cannot silently disappear.

Limits: source<=1 MiB; <=128 functions; <=1024 nodes/function; dependency depth<=256; <=64 params; JSON depth<=128; <=100,000 collection elements; call depth<=64; <=1,000,000 node evaluations/root call; HTTP input<=1 MiB; file/value input/output<=16 MiB; integer magnitude<=65,536 bits. Size-growing primitives check prospective sizes before allocation where the bound is computable. All child calls/maps/folds/attempts share one budget. Limits raise `resource_limit`, never partial success. Resource-limit and cancellation errors cannot be caught to reset a budget.

## One canonical operation catalog

`mairg/language.py` owns OPS and HOSTS descriptions/arity. Every implementation dispatch is mechanically checked against them. All node args below are references, never embedded expressions. Unknown fields reject.

| Op | Args | Extra fields | Meaning |
|---|---:|---|---|
| param |0| index:int | Current function argument |
| const |0| value:JSON | Fresh immutable-semantics value |
| record |N| keys:N distinct strings | Construct record |
| list |N| none | Construct list |
| field |2| none | Record, string key; missing fails |
| index |2| none | List/string, nonnegative in-range int |
| set_field |3| none | Record, key, value -> new record |
| length |1| none | String/list/record length |
| concat |2| none | Two strings or two lists of identical kind |
| keys |1| none | Record keys sorted by Unicode codepoint |
| contains |2| none | String substring, list strict structural equality, or record string-key membership |
| eq |2| none | Strict typed structural JSON equality |
| lt/le/gt/ge |2| none | Same numeric kind or two strings |
| add/sub/mul |2| none | Two ints or two floats of same kind |
| div |2| none | Two numbers, nonzero divisor -> finite float; explicit division defines conversion |
| mod |2| none | Two ints; remainder has divisor sign; zero fails |
| neg |1| none | Int/float negation |
| and/or |2| none | Eager bool operations |
| not |1| none | Bool negation |
| text.trim/lower/upper |1| none | Unicode string operation |
| text.split |2| none | Text and nonempty separator |
| text.join |2| none | Separator and list of strings |
| text.replace |3| none | Text, nonempty old text, new text |
| json.parse |1| none | Strict JSON string decode |
| json.stringify |1| none | Compact sorted-key JSON text |
| parse_int |1| none | Decimal string, optional minus, no whitespace/underscores/exponent |
| to_text |1| none | String unchanged; other JSON uses compact JSON |
| asset.text |1| none | Declared relative asset -> immutable snapshotted UTF-8 text |
| call |N| target:functionID | Reusable function call |
| choose |1+N| then/else:functionIDs | Strict bool then one selected function with remaining args |
| map/filter |1| target:functionID | List; pure unary callback; filter actually returns bool |
| fold |2| target:functionID | List, initial accumulator; ordered callback(acc,item), may have effects |
| attempt |N| target:functionID | Call once; tagged success/failure |
| fail |2| none | String code/message -> application error |
| host |N| binding:string | Declared generic host binding |
| transaction |N| target:functionID | DB-only function in actual SQLite transaction |

Calls/branches/callback arities validate before execution. Runtime checks each actual argument/result type; branch/fold signature types need not be textually identical. Attempt returns `{ok:true,value:JSON}` or `{ok:false,error:{code,message,function,node,details}}`; it catches target errors only, after its own arguments are evaluated. Resource exhaustion/cancellation propagate.

## Compilation and effects

Compile live nodes into straight-line Python AST assignments in dependency order, one assignment per live node in a fresh frame. Shared producers execute once. Choose/attempt bodies are separate function calls and remain lazy. A failure aborts the frame, so no failed effect is replayed for a second consumer. Distinct call nodes are separate invocations. No thunk VM, exception cache, speculative CSE, or partial-op constant folding is needed.

All user data becomes AST literals, never Python expressions or identifiers. Internal names are compiler-assigned. Compile/unparse produces a real generated program module; building is not merely copying JSON. Independent failing nodes have unspecified error precedence. Declaration order/opaque IDs cannot impose causal order or stable business identity. ID renaming preserves successful behavior and the declared partial-order constraints on effects; on failure, any completed unordered effects may remain, so source must add dependencies when that ordering matters; generated bytes/source diagnostic IDs need not be identical.

Infer function effects to fixed point: `(db|file, read|write)`. Host calls use their underlying resource domain, not binding aliases. Transaction is db/write. Calls/choose/map/filter/fold/attempt propagate effects. Pure map/filter callbacks have empty transitive effect sets. In a live function, two nodes sharing a domain require a dependency path when either may write; otherwise reject. Two reads may be unordered. Collection fold explicitly supplies sequential recurrence. Never choose write ordering from declaration position or node IDs.

No exactly-once claim across crashes/calls, automatic retries, cross-resource rollback, or sandbox. Confirmed DB commits are not reversed by a later file/response failure. Request-local frames, budgets, host connections, and mutable values are isolated; callers cannot mutate future constant/result values through returned references.

## Project and resources

`mairg.json` has exactly `format`, `name`, `program`, `bindings`, `database`, `assets`, `routes`, `startup`, `tests`. Format=`mairg-project-1`. Program is one project-relative source file. Bindings maps external binding names to HOSTS builtin names. Database is null or one project-state-relative SQLite path. Assets is an array of project-relative UTF-8 files snapshotted at compilation; no directories. Routes is an array of `{method,path,export}`. Startup is null or a zero-argument exported function, called before serving. Tests is an array of project-relative test-fixture files.

Program/asset/test paths are canonical relative paths confined to project root; reject `..`, absolute paths, escaped symlinks and canonical duplicates. Source/project loading/checking performs no application effects. App schema/migration files are assets, never autoexecuted by MAIRG.

File and DB effects require an explicit `--state-dir`; no CWD/source-tree fallback. Generic file paths resolve only under STATE_DIR/files; database paths resolve only under STATE_DIR/db. These canonical subtrees must be disjoint, inside the chosen state root, and cannot alias through symlinks. Generic file hosts cannot reach database files or SQLite sidecars. One canonical DB identity owns all SQL effects. Source/assets are separate immutable snapshots and must never be mistaken for writable state. Resource directories may be created only during explicit execution after invocation arguments validate; checking/describing/building does not create app state.

## Host catalog and transaction owner

| Builtin | Args -> result | Effect |
|---|---|---|
| file.read_text | str -> str, strict UTF-8, bounded | file/read |
| file.write_text | str,str -> null; atomic replacement | file/write |
| file.read_csv | str -> list of string records; distinct nonempty headers/exact row width | file/read |
| sqlite.query | str,list -> list of records; parameterized/read-only | db/read |
| sqlite.execute | str,list -> `{rows:list,changes:int}`; RETURNING rows supported | db/write |

No application/table/route/business-field names occur in host dispatch. CSV normalization/conversion/policy is application logic. SQL values are parameters, not interpolated source. SQLite bindings accept null/bool/int64/finite float/str; bool maps to0/1 only at this defined protocol boundary. Compound values/big ints/BLOB or nonfinite returned values reject. Query column names must be unique, never silently overwritten.

Read-only SQL is enforced with SQLite authorizer actions, including rejection of INSERT RETURNING, transaction control, ATTACH/DETACH, writes/DDL and write PRAGMAs. Execute also denies user transaction control, ATTACH/DETACH, and changes to adapter-owned durability/foreign-key settings. Do not classify SQL by prefix. App-owned PRAGMA user_version is allowed where safe. Each call executes one statement; multiple statements reject. Builtin extensions that escape this owner are not enabled.

One SQLite connection per standalone SQL operation, or one shared connection per explicit transaction region. Use `isolation_level=None`, foreign_keys=ON, synchronous=FULL, bounded busy timeout, and owner-issued BEGIN IMMEDIATE/commit/rollback outside the user-SQL authorizer. Standalone execute owns one transaction. Transaction target's transitive effects must be only db reads/writes, with no nested transaction; reject statically/dynamically. Asset reads/pure computation are allowed. Function return/result JSON is checked before region commit. Schema/result/SQL/error/cancellation failures rollback before connection close. Failed/unknown commit is never retried or described as a guaranteed rollback unless rollback was confirmed; preserve the primary error if cleanup also fails.

File write uses a private same-directory temp, UTF-8 write, flush/fsync, then os.replace. Failure before replacement preserves existing file and removes owned temp. Failure after replacement may be unknown durability, never false success or hidden retry. The containing state-root/path identity is verified before use. No distributed atomicity is claimed between DB and files.

## WSGI and server

Use Waitress 3.0.2, not a custom HTTP protocol server. WSGI handler input is exactly `{method,path,params,query,headers,body}`; query values are arrays of strings, request headers lowercase, body is strict JSON or null. Malformed/oversize input rejects before application effects. Result is exactly `{status:int,headers:map<str>,body:JSON}`. Status 200..599. web.py exclusively owns response framing: application Content-Length, Content-Type, Connection, Keep-Alive, Proxy-Authenticate, Proxy-Authorization, TE, Trailer/Trailers, Transfer-Encoding, and Upgrade reject case-insensitively. Other header names must be ASCII HTTP tokens; values must encode as Latin-1 and contain no forbidden controls/CR/LF/NUL. The host serializes body to UTF-8 JSON and derives Content-Length/Content-Type before start_response. 204/304 require null body and emit no body bytes.

Routes use whole-segment `{name}` parameters. Literal segments take precedence; equal-specificity overlapping patterns reject. Matching is independent of manifest order. Exact method routes only. Standard404/405/400/413/500 errors are generated by transport; business statuses/rules are authored in graphs. No handler replay on disconnect. Plans are shared immutable data; every request has a new execution owner. Startup runs successfully before socket readiness.

Actual Waitress cleanup: close server and task_dispatcher.shutdown(timeout=2); this stops acceptance/pending work and gives active handlers a bounded drain, not a claim that Python threads can be forcibly killed. Tests own the server process and classify in-flight outcomes without replay; exact assigned loopback ports in tests. A normal WSGI callable/factory is exported for reuse. This POC is not a production deployment/security claim.

## Shared implementation APIs

- `language.py`: `AppError(code,message,*,function=None,node=None,details=None)`, `.as_dict()` returns `{"error":...}`; strict decode/check JSON/type helpers; OPS/HOSTS canonical metadata.
- `compiler.py`: `compile_project(root)->CompiledProject`; `compile_documents(manifest,document,*,root,assets)->CompiledProject`; validate raw documents every time. Project exposes copied manifest/document/assets, root, source_revision, generated_source, describe(). No caller-constructed typed-IR trust boundary.
- `runtime.py`: CompiledProject.call(export,arguments,*,state_dir=None)->JSON and generated-function execution, budgets, pure/control ops. Each call creates an independent HostRuntime and closes it in finally. Compiler/runtime may place CompiledProject class in either file but publish it from compiler.py.
- `hosts.py`: `HostRuntime(project,*,state_dir=None)`, `.call(binding,args)->JSON`, `.transaction()` context manager, `.close()`. Only generic adapters and actual resource ownership. Language runtime invokes transaction context around the target call.
- `web.py`: `make_wsgi_app(project,*,state_dir=None)->callable`; `serve_project(project,host,port,*,state_dir=None)` Waitress lifecycle.
- `project.py`: new/test/build/patch/source-revision helpers. No generic library/plugin implementation in this POC.
- `cli.py`/`__main__.py`: transport for commands below.

Builders coordinate ordinary internal symbol choices; changes to owners/identities/semantics require root model revision. No app-specific Python callback is an implementation shortcut.

## Tool workflow and build

Commands: `mairg new DIR [--template cli|api]`; `describe PROJECT`; `check PROJECT`; `run PROJECT EXPORT --args JSON [--state-dir DIR]`; `serve PROJECT --host127.0.0.1 --port8000 --state-dir DIR`; `test PROJECT`; `build PROJECT --output DIR`; `patch PROJECT PATCHFILE`. Run args is a JSON array. Success emits one JSON result; errors structured JSON on stderr/exit 2; cancellation 130. Server is the long-running exception.

Fixture file is exactly `{files:{relativePath:text},cases:[...]}`. Each fixture gets a fresh owned state dir, initial files, and ordered cases. Cases are `{export,args,expect}` or `{export,args,error:code}`. Unexpected failure stops that fixture (remaining cases not run); independent fixtures can continue. Never mutate a user's DB for testing.

Patch is exactly `{base,program}`: source revision over canonical manifest+whole source including IDs+asset hashes; replacement program validates against the whole existing project in memory. Return replacement JSON only after success. No file writes/partial mutation. Canonical JSON is sorted keys, ensure_ascii=True, compact separators; functions/nodes sorted by ID for revision so declaration shuffling leaves revision unchanged. ID renaming changes the edit revision, not meaning.

Build validates source and emits a directory with generated program.py (functions plus sole executable metadata), runtime package, bundled Waitress, immutable assets, bundle manifest/digests, and run.py. No third-party project dependencies in this POC. No MAIRG install or C compiler is required at runtime; Python 3.11+ is required. `python OUTPUT/run.py EXPORT --args ... --state-dir ...` and `python OUTPUT/run.py serve ...` run outside checkout. Bundle execution uses generated program/metadata only, never falls back to recompiling stale source. It checks its own format/runtime/digests and fails clearly on mismatch.

Build owns a sibling private staging dir and a per-output lock; existing output rejects. Publish only after import/describe smoke by atomic directory rename under that owner. Cooperative concurrent builds cannot overwrite each other; output namespace must not be concurrently mutated by other tools. Failure/cancellation cleans stage/lock; SIGKILL cleanup is not guaranteed. No target is reported built before publication.

## Practical acceptance and comparison

1. One graph-defined inventory/reservation API, with CLI init/list helpers: custom schema and string validation; CRUD; atomic stock decrement+reservation insert; insufficient-stock outcome; rollback after later failure; parameter data with quotes; two concurrent reservations cannot oversell; restart preserves confirmed data. Actual Waitress HTTP and built artifact both exercised. App schema.json and migrations/001.json contain SQL-statement arrays; canonical schema is schema.json. Apply real migration to local fixture DB and prove live schema/master equivalence. Initial migration/master equivalence, existing version1 rows across restart, failed initialization rollback, and explicit rejection of unsupported/newer schema versions are required. Do not invent a version2 migration for a POC that has no historical app schema. No framework schema or hidden initializer.
2. A fresh test agent authors a different useful CLI application after source/reference freeze, using documented graph operations and init/build tooling only. No runtime edit or business-logic Python adapter. It processes real files and runs its built artifact outside checkout. A capability gap is a POC failure to fix at its owner.
3. Compare the complete API/CLI behavior with equivalent Python using the same Waitress/SQLite/file semantics and inputs. Record equality, rollback/restart, startup/readiness, whole-app request/CLI latency, build size/time and honest overhead. No microbenchmark or unmeasured AI-productivity claim replaces useful app proof.
