#ifndef CAPTURE_H
#define CAPTURE_H

#include <stdbool.h>
#include <stdint.h>

// The raw hit capture (console x, read by tools/hits.py and tools/calibrate.py):
// once armed, the next threshold crossing on any pad has the raw samples of
// every input of the Pico's own ADC (GP26-GP28) copied, CAPTURE_US in all, the first 3/8 of it before the
// crossing (a hit on a damped practice pad rises for up to 1 ms before it gets
// there). Arrival times and strengths are what locating a hit works from.
//
// Printed as "X pad <i> adc <adc> rate <r> slot_ns <ns> before <n> samples <n>",
// "X inputs <adc>... slots <slot>...", one row per sample "<k> <v>...", "X end".

void capture_arm(void);                         // core 0
bool capture_armed(void);                       // core 1: worth reporting a crossing?
void capture_trigger(uint8_t adc, uint32_t index, uint8_t pad);  // core 1: a crossing at `adc`'s sample `index`
void capture_scan(void);                        // core 1, after the inputs: copies once the samples are in
void capture_poll(void);                        // core 0: prints a finished capture, a few rows at a time

#endif
