// Independent, handwritten fused C++17 reduction.
#include <cstddef>
#include <cstdint>
extern "C" void mairg_kernel(std::size_t n, const std::uint64_t *const *cols, std::uint64_t *out) {
    std::uint64_t total=0;
    for (std::size_t i=0; i<n; ++i) total+=cols[2][i];
    out[0]=total;
}
