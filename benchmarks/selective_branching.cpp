// Additional idiomatic branching comparator; the tuned primary is selective.cpp.
#include <cstddef>
#include <cstdint>
extern "C" void mairg_kernel(std::size_t n, const std::uint64_t *const *cols, std::uint64_t *out) {
    std::uint64_t count = 0, bytes = 0;
    for (std::size_t i = 0; i < n; ++i) {
        if (cols[0][i] >= 500 && !cols[3][i]) {
            count += 1;
            bytes += cols[2][i];
        }
    }
    out[0] = count;
    out[1] = bytes;
}
