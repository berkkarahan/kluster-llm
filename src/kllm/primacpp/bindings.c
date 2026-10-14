/* Placeholder C bindings for primacpp.
 * The functions should mirror the llama.cpp API (e.g., llama_load_model, llama_infer).
 * Implementations will be added as the integration progresses.
 */

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

// Example stub function signatures matching llama.cpp's C API.
void* primacpp_load_model(const char* path) {
    // TODO: Implement actual model loading.
    return NULL;
}

int primacpp_infer(void* model, const char* prompt, char* output, size_t out_len) {
    // TODO: Perform inference and write result to output buffer.
    if (output && out_len > 0) {
        output[0] = '\0';
    }
    return -1; // indicate not implemented
}

#ifdef __cplusplus
}
#endif
