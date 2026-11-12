# Measured comparison

MAIRG generated kernels matched hand-tuned C++ closely on all three workloads. At one million rows, every median was within 2% of the tuned baseline. Treat these small differences as parity, not evidence of a language-level speed advantage.

Frozen source: `1f37efaa9a3757bf79ab017d2221ff89db0093fa`; both reports record a clean tree. Runtime source is unchanged by the later documentation/results commit. Hardware: 10 logical CPUs, ARM64 macOS 15.6.1, 64 GiB host memory; Python 3.14.7 and Apple Clang 17. Both kernels use the same compiler family, `-O3`, equivalent single-threaded native code, identical positional input buffers, and output checks.

## Primary: hand-tuned C++

Prepared, warm, in-memory kernel medians; parsing, validation, compilation, and serialization are excluded. Three warmups and eleven interleaved samples per size; calls are calibrated and final outputs checked/consumed. Raw samples, ranges, MAD, hashes, compiler commands, seeds, and resource scopes are in [primary JSON](results/primary-macos-arm64.json).

| Workload | Rows | MAIRG (ms) | Tuned C++ (ms) | MAIRG / C++ time |
|---|---:|---:|---:|---:|
| simple | 10,000 | 0.001331 | 0.001330 | 1.001 |
| simple | 100,000 | 0.009940 | 0.009980 | 0.996 |
| simple | 1,000,000 | 0.102584 | 0.102793 | 0.998 |
| selective | 10,000 | 0.003581 | 0.003606 | 0.993 |
| selective | 100,000 | 0.029848 | 0.029800 | 1.002 |
| selective | 1,000,000 | 0.397529 | 0.404551 | 0.983 |
| telemetry | 10,000 | 0.006113 | 0.006193 | 0.987 |
| telemetry | 100,000 | 0.056455 | 0.055969 | 1.009 |
| telemetry | 1,000,000 | 0.566028 | 0.567556 | 0.997 |

Lower ratios mean less MAIRG time. Every output agreed exactly with the independent reference; no case exceeded the 25% regression investigation threshold. These results are from one machine, one compiler configuration, one seed, and warm repeated buffers. They do not establish superiority on other machines or workloads.

## Additional: ordinary branching C++

The additional selective implementation uses a conventional conditional branch. It is explicitly separate from the stronger tuned primary. The source is [selective_branching.cpp](../benchmarks/selective_branching.cpp). Both versions compute the same result.

| Rows | MAIRG (ms) | Branching C++ (ms) | C++ time / MAIRG time |
|---:|---:|---:|---:|
| 10,000 | 0.004265 | 0.005148 | 1.21x |
| 100,000 | 0.029765 | 0.229984 | 7.73x |
| 1,000,000 | 0.400840 | 2.598529 | 6.48x |

At one million rows this is about 6.48x faster **for this selective kernel versus this branching implementation**. MAIRG lowers pure boolean/masked reductions into a form the native compiler can vectorize. Independently hand-tuned C++ achieves the same improvement, as the primary comparison demonstrates. This is neither a limit of C++ nor a 6.48x whole-application speedup. [Additional raw JSON](results/branching-macos-arm64.json).

## Cold end to end

Fresh processes, 10,000 JSONL records, three samples each. Both sides include the identical Python host parser, full validation, buffer creation, their respective build, library loading, execution, and JSON output. Data generation is excluded. These are compilation-inclusive CLI invocations, not steady-state service latency.

| Workload | MAIRG median (ms) | Tuned C++ median (ms) |
|---|---:|---:|
| simple | 337.552 | 337.969 |
| selective | 336.477 | 337.523 |
| telemetry | 337.182 | 337.732 |

The common host and compilation dominate this small cold CLI example. The kernel gain against the branching comparator does not translate into a material cold-process gain.

## Build and resource scopes

Compiler-only times run from process launch through successful exit on both sides. Total times include source read, validation/lowering as applicable, native compilation, library load, and ABI setup. Single build observations are descriptive, not statistical build-speed claims.

| Workload | MAIRG compiler (ms) | C++ compiler (ms) | MAIRG total (ms) | C++ total (ms) |
|---|---:|---:|---:|---:|
| simple | 57.617 | 64.252 | 200.393 | 178.426 |
| selective | 63.826 | 63.720 | 180.368 | 180.897 |
| telemetry | 60.505 | 67.771 | 175.980 | 186.146 |

Benchmark-host cumulative peak RSS: 523,075,584 bytes (498.8 MiB). This includes data generation and reference interpretation; it is not per-language memory or kernel allocation. Both kernels have no intermediate column buffers. Library sizes and exact checksums are in the raw report; binary bit-for-bit reproducibility is not claimed.

## What the language changes

The telemetry program contains 18 source nodes and compiles to 11 after constant folding, exact CSE, and dead-node elimination. All seven statistics share one traversal. Its authors supply no loops, temporary columns, branch schedule, meaningful internal variable names, or classes. The compiler applies the same generic masking rule to every graph; it contains no telemetry/workload-name special case.

This demonstrates native performance with compiler-owned execution choices. Graph IR, fusion, and CSE are prior art. AI generation success, token cost, repair success, and maintenance efficiency remain unmeasured. Serialized source size is not a model-productivity result. Controlled equal-budget, held-out agent trials are the next experiment before claiming an AI-authoring advantage.

## Reproduce

```sh
git checkout 1f37efaa9a3757bf79ab017d2221ff89db0093fa
python3 -m unittest discover -s tests -v
python3 benchmarks/run.py --sizes 10000 100000 1000000 --repetitions 11 --warmups 3 --seed 17 --e2e-rows 10000 --e2e-repetitions 3 --output /tmp/mairg-primary.json
python3 benchmarks/run.py --workloads selective --cpp-style branching --sizes 10000 100000 1000000 --repetitions 11 --warmups 3 --seed 17 --e2e-rows 10000 --e2e-repetitions 3 --output /tmp/mairg-branching.json
```

Run on a quiet machine and retain unfavorable results. See [benchmark methodology](../benchmarks/README.md) and [validation](VALIDATION.md).
