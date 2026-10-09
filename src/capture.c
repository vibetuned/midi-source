#include "capture.h"

#include <stdio.h>

#include "analog.h"
#include "pico/stdlib.h"

#define CAPTURE_US 4000
#define CAPTURE_MAX_SAMPLES 512

typedef enum { CAPTURE_OFF, CAPTURE_ARMED, CAPTURE_WAITING, CAPTURE_DONE } phase_t;
static volatile phase_t phase;  // armed by core 0, advanced by core 1, printed by core 0
static uint32_t index0, before, total, rate;
static uint8_t pad, adc;
static size_t row;
static bool ok;
static uint8_t inputs[3];
static size_t input_count;
static uint16_t samples[3][CAPTURE_MAX_SAMPLES];

void capture_arm(void) {
    uint8_t all[ANALOG_MAX_INPUTS];
    size_t n = analog_inputs(all);
    input_count = 0;
    for (size_t k = 0; k < n; k++)  // the Pico's own inputs: one sample clock, one row per sample
        if (analog_adc(all[k]) == ANALOG_PICO) inputs[input_count++] = all[k];
    if (input_count == 0) {
        printf("no GP26-GP28 inputs in the layout\n");
        return;
    }
    rate = analog_rate(inputs[0]);
    total = MIN((uint32_t)((uint64_t)rate * CAPTURE_US / 1000000), CAPTURE_MAX_SAMPLES);
    before = total * 3 / 8;
    phase = CAPTURE_ARMED;
    printf("Capturing the next hit (raw samples, %lu us before to %lu us after)...\n",
           (unsigned long)(before * 1000000ull / rate), (unsigned long)((total - before) * 1000000ull / rate));
}

bool capture_armed(void) {
    return phase == CAPTURE_ARMED;
}

void capture_trigger(uint8_t a, uint32_t index, uint8_t p) {
    if (phase != CAPTURE_ARMED) return;
    // On the Pico's inputs' clock, whichever ADC saw the crossing.
    index0 = analog_adc(a) == ANALOG_PICO ? index : analog_index(inputs[0], analog_time(a, index));
    pad = p;
    adc = a;
    phase = CAPTURE_WAITING;
}

void capture_scan(void) {
    if (phase != CAPTURE_WAITING) return;
    const analog_block_t *b = analog_block(inputs[0]);
    uint32_t first = index0 - before;
    if ((int32_t)(b->first + (uint32_t)b->count - (first + total)) < 0) return;  // not all in yet
    ok = true;
    for (size_t k = 0; k < input_count; k++) ok &= analog_history(inputs[k], first, total, samples[k]);
    row = 0;
    phase = CAPTURE_DONE;
}

void capture_poll(void) {
    if (phase != CAPTURE_DONE) return;
    if (row == 0) {
        if (!ok) {
            printf("X failed: the samples were already overwritten\n");
            phase = CAPTURE_OFF;
            return;
        }
        printf("X pad %u adc %u rate %lu slot_ns %u before %lu samples %lu\n", pad, adc, (unsigned long)rate,
               ANALOG_SLOT_NS, (unsigned long)before, (unsigned long)total);
        printf("X inputs");
        for (size_t k = 0; k < input_count; k++) printf(" %u", inputs[k]);
        printf(" slots");
        for (size_t k = 0; k < input_count; k++) printf(" %u", analog_slot(inputs[k]));
        printf("\n");
    }
    // A few rows per main-loop pass, so printing never holds MIDI up for long.
    for (size_t n = 0; n < 32 && row < total; n++, row++) {
        printf("%u", (unsigned)row);
        for (size_t k = 0; k < input_count; k++) printf(" %u", samples[k][row]);
        printf("\n");
    }
    if (row == total) {
        printf("X end\n");
        phase = CAPTURE_OFF;
    }
}
