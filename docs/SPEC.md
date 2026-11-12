# MAIRG 1 specification (initial release)

Status: approved pre-edit specification by all three independent reviewers. Canonical model: MAIRG-M2, revision 2.

## Objective and first target

A language for machine-authored deterministic batch workers. Canonical programs are dependency graphs, not sequential source. The compiler owns ordering, shared-expression elimination, dead-code removal, constant folding, and fused traversal. No handwritten loops, mutation, classes, user scheduling, or meaningful internal variable names are required. JSON is an interoperable transport, not a claim of optimal AI tokenization. Symbolic opcodes retain model vocabulary; their benefit remains a testable hypothesis.

This release is deliberately a domain-specific compiled language, not a universal language. It computes unsigned telemetry statistics. It does not handle floats, signed numbers, strings as computational values, arbitrary functions, recursion, I/O, concurrency, division, shifts, or persistent state. These are future design questions, not hidden fallbacks. External effects belong to the host.

## Source document

Exactly four keys: `format`, `inputs`, `nodes`, `outputs`. Format is exactly `mairg-1`. All arrays below are actual JSON arrays. Unknown keys, duplicate JSON keys, NaN/Infinity, malformed JSON, excessive JSON nesting (including parser RecursionError), and unsupported format are structured errors.

Inputs: ordered array of `[external_name, type]`; types are `u64` or `bool`. Names are nonempty strings of at most 128 Unicode code points and must be unique within inputs. Output names follow the same rule and are unique within outputs. Input and output namespaces are distinct. Names are never inserted into native source: ABI positions carry identities.

Nodes: unordered array of `[id, opcode, ...operands]`. IDs are opaque integers in `[0, 2^31-1]`; booleans are not IDs. IDs must be unique. Forward references are valid. Every node, including unreachable nodes, must be well typed and acyclic. Node order and ID spelling have no meaning beyond references.

Limits: UTF-8 source <= 1 MiB; 1..128 inputs; 1..4096 nodes; 1..128 outputs; dependency depth <=256. The longest path counts nodes, including leaves. Validate iteratively, without recursive graph traversal. The in-memory `validate(dict)` API applies the same structural limits; serialized bytes are checked by `loads`/`load`.

## Scalar operations

One canonical operation registry in `mairg/ir.py` defines arity and typing for validation, optimization dispatch coverage, and documentation/tests.

| Opcode | Operands | Result |
|---|---|---|
| `input` | input-position integer | declared input type |
| `u64` | integer literal 0..2^64-1 | u64 |
| `bool` | JSON boolean literal | bool |
| `add`, `sub`, `mul`, `bit_and`, `bit_or`, `bit_xor` | two u64 node references | u64 |
| `eq` | two references of identical scalar type | bool |
| `lt`, `le`, `gt`, `ge` | two u64 references | bool |
| `and`, `or` | two bool references | bool |
| `not` | one bool reference | bool |
| `select` | bool condition, two references of identical type | branch type |

Every arithmetic operation and sum is modulo 2^64, including subtraction. No implicit conversions. Inputs reject Python bool as u64, floats, negative numbers, out-of-range integers, strings, nulls, and non-bool values for bool. All operations are total for valid types; both select branches are pure/total, so evaluation order is unobservable.

## Outputs and batch semantics

An output is one of:

- `[name, "count", predicate_node]`
- `[name, "sum", u64_node, predicate_node]`
- `[name, "max", u64_node, predicate_node]`
- `[name, "xor", u64_node, predicate_node]`

Predicates must be bool. All results are u64. `count` counts selected rows modulo 2^64; `sum` adds selected values modulo 2^64; `max` finds the greatest selected u64; `xor` bitwise-XORs selected values. All four identity results are zero, including empty input or no matching rows. Output declaration order is preserved in the result and ABI.

Public column input is a mapping with exactly the declared input names; columns are list/tuple sequences, equal length, and strictly typed. An empty batch still supplies all columns. Validate the whole batch before calling native code or returning results. Record input similarly requires each row to have exactly the declared fields. One invalid row rejects the entire invocation; no partial aggregates are returned. No coercion or default fields.

## Canonical plan and structural editing

Validation precedes optimization. Remove unreachable valid nodes, fold literal-only operations, intern exact structural keys for common subexpressions, canonicalize lawful commutative operands, and emit deterministic dependency order with compiler-assigned IDs. `add`, `mul`, bitwise binary operations, `eq`, `and`, `or` are commutative; no other operation is. No speculative or floating-point rewrites.

Plan fingerprint is SHA-256 of the canonical optimized document. Equivalent source declaration order and consistently renamed opaque IDs produce the same plan/fingerprint. Output/interface order and names remain contractual. SHA-256 is an identity checksum, not a proof of correctness; CSE uses exact structural keys, not hash equality.

Canonical bytes are exactly `json.dumps(document, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")`. Source revision is SHA-256 of these bytes for the source document with node declarations sorted numerically by source ID. It includes unused valid nodes and original IDs. Source revision identifies an edit base; plan fingerprint identifies compiled semantics. These are separate concepts.

A patch is an object with required `base` (source revision), optional `set_nodes` (upsert node rows), optional `remove_nodes` (IDs), and optional `outputs` (entire replacement output list). No other keys. No duplicate or overlapping mutation IDs. Removal requires the ID to exist. Set replaces an existing node or creates a new one. Apply all edits to a fresh document, then validate the complete result. Missing/wrong base, invalid final graph, or any invalid edit rejects the entire patch. Input schema is not patchable. No source file is mutated: the CLI writes the valid result to stdout only after success. This is an atomic in-memory transaction, not a distributed mutation protocol.

## Public Python interface and owner boundaries

Public consumers (`source_revision`, `optimize`, `apply_patch`, `interpret`, `emit_c`, `compile_program`, and column/batch preparation) re-establish validity through the one `ir.py` validator before using a `Program`. Frozen dataclasses and type annotations are not proof of validation. A `checked_program(program)` helper serializes and validates through that owner, then checks all supplied nonserialized metadata against the inferred reconstruction: each Node.type must exactly match its inferred type and each Output (including count.value=None) must exactly match its reconstructed metadata. Mismatches are rejected, never silently repaired or dropped. Consumers use only the reconstructed validated Program after comparison succeeds; private inner helpers may consume the resulting validated object to avoid repeated checks inside loops. Forged types, IDs, literals, and graphs are rejected as structured errors.

`mairg/ir.py` owns frozen `Node(id, op, args, type)`, `Output(name, op, value, where)`, `Program(inputs, nodes, outputs)`, operation/reduction catalog, `loads(text)`, `load(path)`, `validate(document)`, `to_document(program)`, and `source_revision(program)`. Nodes in a Program are topologically ordered. `Output.value` is None for count; other outputs reference u64 nodes. `args` contains literal/index values for leaf ops and node IDs for other ops. `MairgError(code, message, path)` provides `as_dict()` returning `{"error":{"code":...,"message":...,"path":...}}`.

`mairg/optimizer.py` owns `optimize(program) -> Plan`, with frozen `Plan(program, fingerprint, stats)`; stats include source/live/optimized node counts, folded/merged/dead counts, one row pass, and zero intermediate column buffers. `mairg/patch.py` owns `apply_patch(program, patch) -> Program`.

`mairg/interpreter.py` independently implements source semantics via `interpret(program, columns) -> dict[str,int]`, plus shared boundary validation `validate_columns(program, columns) -> (columns_in_schema_order, row_count)` and `records_to_columns(program, records)`. Boundary checks may be shared; numerical evaluation must not call optimizer or codegen evaluation helpers.

`mairg/native.py` owns `emit_c(program) -> str`, `compile_program(program, optimize_enabled=True) -> CompiledProgram`, native buffers, compiler process, library loading, and cleanup. `CompiledProgram` is a context manager with `prepare(columns) -> PreparedBatch`, `run(columns) -> dict`, `run_prepared(batch) -> dict`, `close()`, `.plan`, and `.source`. A prepared batch pins ctypes buffers and declared input schema; another schema is rejected. Invocation after close is rejected. Batches may be reused across optimized/traditional kernels with the identical positional schema, but never across different schemas. Output buffers are fresh per call. Concurrent close/use is unsupported; this first host is synchronous and docs must say so. Raw ctypes internals are a trusted integration surface, not a security boundary.

Native ABI: `void mairg_kernel(size_t n, const uint64_t *const *cols, uint64_t *out)`. Bool columns use validated 0/1 u64 storage. All operands and accumulators are unsigned. Generated source uses fixed templates, validated numeric literals, and compiler-generated names only. It contains one row loop for all outputs and no intermediate column arrays. Native compiler uses subprocess argv without a shell, fixed optimization flags `-O3 -std=c11 -fPIC` and platform library flags, bounded 60-second compile timeout, and no user-supplied flags. Start the compiler in an isolated process session/group on macOS/Linux. The build owner tracks that group; on timeout or exception/cancellation it terminates and reaps the group with bounded TERM then KILL cleanup (at most 2 seconds each) before deleting its directory. No automatic retries. The compiler is trusted not to detach descendants from its owned group; this is lifecycle ownership, not hostile-process containment. Compiler executable may be explicitly selected as an executable path; it is trusted. No eval, arbitrary code nodes, native imports, or dynamic opcode plugins.

Each build owns a fresh private temporary directory, writes generated source there, compiles there, and loads a library only after compiler exit success. Any validation/build/load failure removes its directory and returns a structured error; no library cache, public runnable output, partial artifact publication, or reused stale build exists. Successful context cleanup releases host references and removes its directory. Dynamic library unloading is platform-managed; callers must not retain raw function handles. It is not a native sandbox. OS/process cancellation may leave OS temporary remnants; do not claim SIGKILL cleanup.

`mairg/cli.py` owns transport only. Commands: `check`, `plan`, `emit`, `run`, `patch`; `python -m mairg` is supported. `run` accepts a graph and a JSONL data file (`-` for stdin), and `--engine native|reference`. Success emits one JSON result; failure emits structured JSON to stderr and exits 2. Other than `emit` (C text), successful machine-facing commands emit JSON. All outputs are buffered until complete success. No HTTP service or UI in this release.

## Application and comparison

Telemetry application graph uses inputs `status:u64`, `latency_us:u64`, `bytes:u64`, `cached:bool`. Outputs: total requests, status>=500 errors, latency_us>=1000 slow requests, total bytes, error bytes, maximum latency, and weighted score sum `(latency_us*3 + bytes)` modulo 2^64. Use multiple related outputs and include deliberate redundant/dead valid nodes to demonstrate optimizer work. Also provide at least two other workloads: simple reduction and selective filtering. The host does not encode telemetry formulas.

Primary baseline: independent hand-written fused C++17 kernel, same ABI/data layout, unsigned semantics, outputs, compiler family, `-O3`, single thread, and hardware. A Python version is secondary. Benchmarks check exact output agreement before measuring; alternate/interleave order with warmups and repeated raw samples; consume results. Record kernel timings on prepared identical buffers separately from fresh CLI end-to-end runs including JSONL parsing/validation/serialization, using the same host parsing/validation path for MAIRG and C++. Separate cold build-inclusive runs from already-built kernel timings. Publish exact commands, compiler flags, dataset seeds, warmup/repetition counts, and hardware/software metadata. Report build times, library size, whole-process peak RSS with platform units (not kernel allocation or a per-language memory claim), and source sizes/descriptive structural metrics. No claimed model-token/productivity benefit without controlled trials; serialized bytes are not token counts. Strong native-baseline parity is acceptable; >25% median regression on large workloads requires investigation and honest reporting, not manipulated inputs or flags.

## Acceptance

All operation/reduction semantics; malformed/dead/cyclic graphs; type and boundary data; overflow/empty/filter boundaries; source reorder/ID rename/canonical fingerprint; patch atomicity/stale base; native/reference/random graph differential tests; build failure/cleanup/closed runtime; code injection resistance; CLI app real workflows; independent baseline equality; reproducible raw benchmark results; package install/smoke; CI on Linux and macOS. No completion claim based on only a generated loop or a Python speedup.

## Prior art

- LLVM: https://llvm.org/docs/LangRef.html
- MLIR: https://mlir.llvm.org/docs/Rationale/Rationale/
- Halide algorithm/schedule separation: https://halide-lang.org/
- Weld analytics optimization: https://github.com/weld-project/weld
- Substrait typed serialized plans: https://substrait.io/spec/specification/
- ctypes semantics: https://docs.python.org/3/library/ctypes.html
- Benchmarking bias: https://llvm.org/docs/Benchmarking.html

Graph IR, fusion, and CSE are established. MAIRG's proposed contribution is their integration with precise agent-facing editing/diagnostics and reproducible evaluation. AI productivity advantage remains unproven until measured.
