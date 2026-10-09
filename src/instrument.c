#include "instrument.h"

#include "analog.h"
#include "capture.h"
#include "inputs/buttons.h"
#include "inputs/pads.h"
#include "inputs/pots.h"
#include "inputs/zones.h"
#include "layout.h"
#include "pico/stdlib.h"
#include "stream.h"

// Written by core 1, read by the console on core 0. Aligned 32-bit accesses
// are atomic on both cores.
static volatile uint32_t scans, scan_max_us, overruns;

// The analog inputs the layout's pots and pads use.
static uint32_t analog_mask(void) {
    uint32_t mask = 0;
    for (size_t i = 0; i < layout.pot_count; i++)
        if (layout_pot_ok(i)) mask |= 1u << layout.pots[i].adc;
    for (size_t i = 0; i < layout.pad_count; i++)
        if (layout_pad_ok(i)) mask |= 1u << layout.pads[i].adc;
    for (size_t i = 0; i < layout.zone_pad_count; i++)
        if (layout_zone_pad_ok(i)) {
            uint8_t inputs[ZONE_MAX_INPUTS];
            size_t n = layout_zone_inputs(&layout.zone_pads[i], inputs);
            for (size_t k = 0; k < n; k++) mask |= 1u << inputs[k];
        }
    return mask;
}

void instrument_core1_main(void) {
    analog_init(analog_mask(), layout_mcp3008_ok() ? layout.mcp3008 : NULL);  // first: pots and pads read its samples
    buttons_init();
    pots_init();
    pads_init();
    zones_init();

    uint64_t next = time_us_64();
    for (;;) {
        uint32_t start = time_us_32();
        analog_scan();
        stream_capture(start);
        buttons_scan(start);
        pots_scan(start);
        pads_scan(start);
        zones_scan(start);
        capture_scan();

        uint32_t took = time_us_32() - start;
        if (took > scan_max_us) scan_max_us = took;
        scans++;

        // Fixed cadence; after an overrun, start again from now rather than
        // racing to catch up.
        next += INSTRUMENT_SCAN_US;
        uint64_t now = time_us_64();
        if (now > next) {
            overruns++;
            next = now;
        } else {
            busy_wait_until(from_us_since_boot(next));
        }
    }
}

instrument_stats_t instrument_stats(void) {
    return (instrument_stats_t){.scans = scans, .scan_max_us = scan_max_us, .overruns = overruns};
}
