#ifndef INPUTS_PADS_H
#define INPUTS_PADS_H

#include <stdint.h>

// Piezo drum pads from the layout, on the ADC pins: a note on per hit, its
// velocity from the hit's peak. Core 1.
void pads_init(void);
void pads_scan(uint32_t now_us);

// Core 0: one line per pad with its hit count and last peak (console p).
void pads_print(void);

// Core 0, console w: arm the recorder for the next hit; poll prints it when done.
void pads_capture_arm(void);
void pads_capture_poll(void);

// Core 0: "H pad <adc> <note> <channel> <threshold> <max> <biased>" per pad,
// for the live stream's header (threshold and max count from the rest level).
void pads_describe(void);

#endif
