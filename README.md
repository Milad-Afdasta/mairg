# MAIRG

[GitHub](https://github.com/Milad-Afdasta/mairg) · [MIT license](LICENSE)

**A compiled language for machine-authored data workers.** Programs describe a typed dependency graph; the compiler owns evaluation order, shared work, constant folding, and fused row traversal.

MAIRG means **Machine-Authored Intermediate Reduction Graph**. This is an initial domain-specific language for deterministic telemetry and batch event analysis. It is a working compiler and software experiment, not a claim to replace every programming language.

## The design question

What should a language look like when a machine is its primary author? Human coding customs are choices to test, not foundations to preserve automatically.

MAIRG removes required handwritten loops, mutable variables, classes, meaningful internal names, source-order execution, manual scheduling, and temporary-column management. Opaque node IDs are references. Forward references and shuffled declarations are valid. External input/output names remain API contracts.

JSON transports the graph and symbolic opcodes use established model vocabulary. Neither choice is claimed to be optimal for models. Readable inspection remains available; artificial obscurity is not an optimization.

## Quick start

Requirements: Python 3.11+, macOS or Linux, and Clang or GCC for native execution. There are no Python runtime dependencies. The reference engine needs no native compiler.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m mairg check examples/telemetry.mairg.json
.venv/bin/python -m mairg plan examples/telemetry.mairg.json
.venv/bin/python -m mairg run examples/telemetry.mairg.json examples/telemetry.jsonl
.venv/bin/python -m mairg run examples/telemetry.mairg.json examples/telemetry.jsonl --engine reference
```

The generic host handles files, JSONL, validation, and output. Telemetry formulas live in the graph. Native execution lowers the graph to C11 and uses the existing native compiler; the language does not require users to write C or choose loops.

## Exact semantics

- Scalars: `u64` and `bool`, with no implicit casts.
- Arithmetic and sums wrap modulo 2^64. Use this for telemetry, not accounting that requires overflow rejection.
- Pure per-row arithmetic, comparisons, boolean operations, and selection.
- Masked `count`, `sum`, `max`, and `xor` reductions; empty results are zero.
- Every supplied node is validated before dead-code removal. Invalid unreachable nodes still reject the program.
- A bad record rejects the whole invocation before native execution. No dropped rows or partial aggregates.
- Structural patches require the exact source revision and validate the entire resulting graph before returning it. A source revision differs from the optimized-plan fingerprint.

Read the [language specification](docs/SPEC.md) for the exact representation, limits, operation catalog, APIs, and failure behavior.

## What is established and what remains a hypothesis

The project must demonstrate exact correctness, automatic dependency ordering/CSE/fusion, a real telemetry application, and a reproducible comparison with an independent optimized C++ implementation. Runtime parity with optimized native code is a useful result.

**Measured on one ARM64 Mac:** the million-row telemetry kernel took 0.566 ms versus 0.568 ms for tuned C++; all three million-row workloads were within 2%. The selective kernel was 6.48x faster than an additional ordinary branching C++ implementation, while tuned C++ matched it. Cold end-to-end timings were essentially equal. [Full methodology and raw results](docs/COMPARISON.md).

AI generation, token efficiency, repair success, and maintenance advantages require controlled model trials. Serialized source size is not token usage. A Python speedup does not establish a language-level advantage. Performance results are added after validation in [the comparison report](docs/COMPARISON.md).

## Boundaries

This first language is pure and deliberately bounded. It has no floating point, general recursion, strings as computational values, database access, arbitrary native imports, network effects, UI toolkit, or persistent state. Broad application support would require further semantics and host integrations.

Generated kernels run as trusted native code, not in a security sandbox. The compiler executable is trusted. Build processes and temporary directories have explicit lifecycle owners; context managers clean successful builds, failure paths clean failed builds, and closed objects reject reuse. Hosts are synchronous; concurrent close/use and raw ctypes misuse are outside the public lifecycle contract. OS library mappings may remain after close. No production service is deployed.

## Development and evidence

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q mairg benchmarks
```

The design passed three independent specification-review lenses before runtime implementation. Three separate builders and three new testing/debugging agents cover the owner boundaries. See [model closure](docs/MODEL_CLOSURE.md), [review evidence](docs/reviews/specification.md), and [execution plan](docs/PLAN.md). CI runs on Linux/macOS with Python 3.11 and 3.14.

## Prior work

Typed graph IR, common-subexpression elimination, fusion, and algorithm/schedule separation are established compiler ideas. Relevant work includes [LLVM](https://llvm.org/docs/LangRef.html), [MLIR](https://mlir.llvm.org/docs/Rationale/Rationale/), [Halide](https://halide-lang.org/), [Weld](https://github.com/weld-project/weld), and [Substrait](https://substrait.io/). MAIRG explores an integrated agent-facing contract and evaluation, not an invention claim for those techniques.
