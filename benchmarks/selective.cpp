// Independent, hand-tuned fused C++17 selective filter/reduction.
#include <cstddef>
#include <cstdint>
extern "C" void mairg_kernel(std::size_t n, const std::uint64_t *const *cols, std::uint64_t *out) {
    std::uint64_t count=0, bytes=0;
    for (std::size_t i=0; i<n; ++i) {
        const std::uint64_t selected = ((cols[0][i] >= 500) & (!cols[3][i]));
        count += selected;
        bytes += cols[2][i] & (std::uint64_t{0} - selected);
    }
    out[0]=count;
    out[1]=bytes;
}
