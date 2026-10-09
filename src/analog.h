#ifndef ANALOG_H
#define ANALOG_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "mcp3008.h"

// The analog inputs, sampled without pause: each ADC cycles through its
// inputs in use and DMA writes every sample into a ring buffer. Each scan,
// analog_scan() splits the samples that arrived since the last scan by input,
// for the pots (which average them) and the pads (which look for peaks).
// Core 1.
//
// Two ADCs: the Pico's own (inputs 0-2, GP26-GP28) at ANALOG_SAMPLE_RATE in
// all, and an optional MCP3008 on SPI (inputs MCP3008_CH(0)-MCP3008_CH(7)).
// Samples are 12-bit from both: an MCP3008's 10 bits are scaled up.
#define ANALOG_SAMPLE_RATE 500000  // the Pico's ADC at its maximum: conversions back to back
#define MCP3008_CH(n) (8 + (n))    // the MCP3008's channel n as an analog input
#define ANALOG_IDS 16              // input numbers are below this
#define ANALOG_MAX_INPUTS 11       // in use at once: GP26-GP28 and CH0-CH7

enum { ANALOG_PICO, ANALOG_MCP3008, ANALOG_ADCS };  // the ADCs

static inline int analog_adc(uint8_t input) {
    return input >= MCP3008_CH(0) ? ANALOG_MCP3008 : ANALOG_PICO;
}

// bit n of `input_mask` = input n. `mcp3008` is the layout's, or NULL.
void analog_init(uint32_t input_mask, const mcp3008_def_t *mcp3008);
void analog_scan(void);

typedef struct {
    const uint16_t *samples;  // 12-bit, oldest first
    size_t count;
    uint32_t first;           // index of samples[0] in this input's stream
} analog_block_t;

// The samples of one input from the latest analog_scan().
const analog_block_t *analog_block(uint8_t input);

// The inputs in use, the Pico's first, ascending; returns how many.
size_t analog_inputs(uint8_t out[ANALOG_MAX_INPUTS]);

// "GP26", "CH0"...
const char *analog_name(uint8_t input);

// Samples per second of one input.
uint32_t analog_rate(uint8_t input);

// When sample `index` of `input` was taken (time_us_32, to a microsecond or
// so), and the index of the sample taken nearest time `t_us`: these line up
// inputs of different ADCs. From the latest scan; for the last 8 ms or so.
uint32_t analog_time(uint8_t input, uint32_t index);
uint32_t analog_index(uint8_t input, uint32_t t_us);
// One input's sample period, microseconds x 65536.
uint32_t analog_period_q16(uint8_t input);

// The inputs of one ADC with sample k were taken in the same round of it:
// input number `slot` of the round. On the Pico's ADC each slot comes
// ANALOG_SLOT_NS after the one before.
#define ANALOG_SLOT_NS 2000
uint8_t analog_slot(uint8_t input);

// Copy `count` raw samples of `input` starting at its sample index `first`
// (the numbering of analog_block_t), from the ring buffer: the last 6 ms or
// so that analog_scan() has already handed out (more for the MCP3008). False
// if they aren't there.
bool analog_history(uint8_t input, uint32_t first, size_t count, uint16_t *out);

typedef struct {
    uint32_t inputs;      // inputs sampled, including a filler to make a power of two; 0 = not running
    uint32_t input_rate;  // samples per second per input
    uint32_t overruns;    // scans that came too late to read every sample
    uint32_t measured;    // samples per second actually arriving, over the last second
    uint8_t bits;         // the ADC's own resolution
} analog_stats_t;

analog_stats_t analog_stats(int adc);

#endif
