# Reproducible comparison

Run from the repository root:

```sh
python3 benchmarks/run.py --sizes 10000 100000 1000000 --repetitions 11 --warmups 3 --seed 17 --e2e-rows 10000 --e2e-repetitions 3 --output results/local.json
```

The primary baseline is independent handwritten, fused C++17 in `simple.cpp`, `selective.cpp`, and `telemetry.cpp`. It uses the same positional ABI, unsigned wraparound semantics, native compiler family, optimization level, threading policy, dataset, and pinned input buffers as the generated C11 kernels. Baseline formulas are not generated from MAIRG. Both implementations must agree exactly with independent source interpretation before any timed samples are collected.

The selective primary is hand-tuned with eager boolean evaluation and unsigned masks. This gives C++ the same optimization opportunity that MAIRG derives automatically from pure, total boolean graph semantics. The additional idiomatic conditional implementation is kept in `selective_branching.cpp`; run it explicitly with `--workloads selective --cpp-style branching`. The default remains the stronger tuned baseline. A speedup against the branching implementation is an implementation comparison, not a limit of C++.

Kernel timings use the same ctypes call path, output-buffer allocation outside the timed region, calibrated repeated calls, warmups, and alternating backend order. Every sample's final result is checked and consumed. `mairg_over_cpp_ratio` greater than 1 means MAIRG took longer. `cpp_over_mairg_speedup` greater than 1 means MAIRG was faster. Raw samples, median, range, and median absolute deviation are retained. The 25% regression list is an investigation signal, not a reason to omit an unfavorable workload.

End-to-end measurements launch a fresh Python process per sample and include the same source loading, JSONL parser, full record validation, buffer preparation, backend compilation, kernel invocation, and JSON serialization. Input generation happens beforehand. These are explicitly **cold, compilation-inclusive** timings, separate from the prepared kernel results. They are not steady-state service latency.

The report records compiler commands/versions, seeds, workload sizes, repetitions/warmups, host platform, Python version, source revision, dirty-tree state, generator/graph/generated-C/C++/library SHA-256 checksums, library sizes, build times, and whole-process peak RSS. RSS is the benchmark process's cumulative peak including input generation and the reference interpreter, in platform-specific units. It does not measure individual kernel allocation or establish a per-language memory advantage.

Source bytes and explicit scheduling syntax are descriptive measures. They are not LLM token counts and do not establish an AI generation or repair advantage. Equal-budget, held-out model trials remain future work. Runtime parity with strong C++ is a useful result: MAIRG authors specify the dependency graph without writing traversal, fusion, temporary-buffer, or scheduling code.
