#ifndef MCP3008_H
#define MCP3008_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// An MCP3008 (10-bit) or MCP3208 (12-bit, same pins) ADC on four GPIOs,
// hardware/blocks/mcp3008.py. A PIO state machine clocks one conversion after
// another (mcp3008.pio), DMA hands it the channel of each, round and round,
// and analog.c collects the results like the Pico's own ADC's.
typedef struct {
    uint8_t miso, cs, sck, mosi;  // the chip's Dout, CS, CLK and Din; sck must be cs + 1
    uint8_t bits;                 // 10 for an MCP3008, 12 for an MCP3208; 0 = 10
} mcp3008_def_t;

typedef struct {
    const volatile void *fifo;  // where each result appears, in the low 16 bits
    unsigned dreq;              // the DMA request that says one is there
    uint32_t rate;              // conversions per second, all channels together
    uint8_t bits;
} mcp3008_source_t;

// Why `def` can't work, or NULL.
const char *mcp3008_problem(const mcp3008_def_t *def);

// Start converting channels[0], channels[1]... channels[count - 1], then again
// from the first, for ever; `count` is 1, 2, 4 or 8. Core 1.
bool mcp3008_start(const mcp3008_def_t *def, const uint8_t *channels, size_t count, mcp3008_source_t *out);

// For checking the wiring (console s, core 0): how often each pin changed while
// watched for `us` microseconds (reading the pins back), and where Dout ended.
// Running, CS changes twice per conversion and CLK 2 x (bits + 7) times; Dout
// only changes if the chip answers (pulled up, it reads high when it doesn't).
typedef struct {
    uint32_t cs, sck, mosi, miso;
    bool miso_high;
} mcp3008_activity_t;
mcp3008_activity_t mcp3008_watch(const mcp3008_def_t *def, uint32_t us);

#endif
