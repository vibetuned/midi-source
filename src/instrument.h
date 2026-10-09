#ifndef INSTRUMENT_H
#define INSTRUMENT_H

#include <stdint.h>

// Core 1: scans every input at a fixed rate and turns it into MIDI, so sensor
// timing never waits on USB.
#define INSTRUMENT_SCAN_US 1000

void instrument_core1_main(void);

typedef struct {
    uint32_t scans;
    uint32_t scan_max_us;  // longest single scan
    uint32_t overruns;     // scans that took longer than INSTRUMENT_SCAN_US
} instrument_stats_t;

instrument_stats_t instrument_stats(void);

#endif
