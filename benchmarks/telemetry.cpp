// Independent, handwritten fused C++17 implementation of the telemetry specification.
#include <cstddef>
#include <cstdint>
extern "C" void mairg_kernel(std::size_t n, const std::uint64_t *const *cols, std::uint64_t *out) {
    std::uint64_t requests=0, errors=0, slow=0, bytes_total=0, error_bytes=0, maximum=0, score=0;
    for (std::size_t i=0; i<n; ++i) {
        const std::uint64_t status=cols[0][i], latency=cols[1][i], bytes=cols[2][i];
        requests+=1;
        errors+=(status>=500);
        slow+=(latency>=1000);
        bytes_total+=bytes;
        if (status>=500) error_bytes+=bytes;
        if (latency>maximum) maximum=latency;
        score+=latency*std::uint64_t{3}+bytes;
    }
    out[0]=requests; out[1]=errors; out[2]=slow; out[3]=bytes_total;
    out[4]=error_bytes; out[5]=maximum; out[6]=score;
}
