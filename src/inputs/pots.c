#include "inputs/pots.h"

#include <stdio.h>

#include "analog.h"
#include "layout.h"
#include "midi_out.h"
#include "pico/stdlib.h"

// Each scan averages the pot's samples since the last scan (about 125, see
// analog.c), smooths that, maps its travel to 14 bits, then to a 7-bit CC with
// hysteresis so a pot resting between two values doesn't flicker.
#define POT_SMOOTHING_SHIFT 3     // exponential average: filtered += (raw - filtered) / 8
#define POT_HYSTERESIS      32    // of 128 per CC step: move a quarter step past the edge

// Where travel ends when the layout gives no .min / .max: about 2% from each
// rail, since pots rarely reach them. Raw 12-bit readings.
#define POT_DEFAULT_MIN 82
#define POT_DEFAULT_MAX 4013
// Measured ends are pulled in by 1% of the travel, so 0 and 127 stay
// reachable through noise and drift.
#define POT_END_INSET_PERCENT 1

#define MAX_POTS 3  // GP26-GP28

typedef struct {
    bool enabled;
    int32_t lo, hi;      // travel, 16-bit scale
    int32_t raw;         // latest averaged reading, 16-bit scale
    int32_t filtered;    // 16-bit scale
    int16_t sent;        // last CC value, -1 before the first
    uint16_t seen_min;   // raw 12-bit extremes since the last calibration start
    uint16_t seen_max;
} pot_state_t;

static pot_state_t state[MAX_POTS];
static size_t count;

static volatile bool calibration_restart;  // set by core 0, cleared by core 1
static bool calibrating;                   // core 0

// The mean of this scan's samples, 16-bit scale; -1 if there were none.
static int32_t average(uint8_t input) {
    const analog_block_t *b = analog_block(input);
    if (b->count == 0) return -1;
    uint32_t sum = 0;
    for (size_t i = 0; i < b->count; i++) sum += b->samples[i];
    return (int32_t)((sum << 4) / b->count);
}

static void set_travel(pot_state_t *st, const pot_def_t *def) {
    int32_t lo = def->min ? def->min : POT_DEFAULT_MIN;
    int32_t hi = def->max ? def->max : POT_DEFAULT_MAX;
    if (hi - lo < 256) {  // a bad measurement: fall back to the defaults
        lo = POT_DEFAULT_MIN;
        hi = POT_DEFAULT_MAX;
    }
    int32_t inset = (hi - lo) * POT_END_INSET_PERCENT / 100;
    if (def->min) lo += inset;
    if (def->max) hi -= inset;
    st->lo = lo << 4;
    st->hi = hi << 4;
}

// 0-16383 across the pot's travel.
static int32_t position(const pot_state_t *st, bool invert) {
    int32_t v = (st->filtered - st->lo) * 16383 / (st->hi - st->lo);
    v = v < 0 ? 0 : v > 16383 ? 16383 : v;
    return invert ? 16383 - v : v;
}

void pots_init(void) {
    count = MIN(layout.pot_count, MAX_POTS);
    for (size_t i = 0; i < count; i++) {
        if (!layout_pot_ok(i)) continue;
        pot_state_t *st = &state[i];
        *st = (pot_state_t){.enabled = true, .raw = -1, .sent = -1, .seen_min = 4095, .seen_max = 0};
        set_travel(st, &layout.pots[i]);
    }
}

void pots_scan(uint32_t now_us) {
    bool restart = calibration_restart;
    if (restart) calibration_restart = false;

    for (size_t i = 0; i < count; i++) {
        pot_state_t *st = &state[i];
        if (!st->enabled) continue;
        const pot_def_t *def = &layout.pots[i];

        int32_t raw = average(def->adc);
        if (raw < 0) continue;
        if (st->raw < 0) st->filtered = raw;  // first reading: no ramp up from 0
        st->raw = raw;
        st->filtered += (st->raw - st->filtered) >> POT_SMOOTHING_SHIFT;

        uint16_t raw12 = (uint16_t)(st->filtered >> 4);
        if (restart) st->seen_min = st->seen_max = raw12;
        if (raw12 < st->seen_min) st->seen_min = raw12;
        if (raw12 > st->seen_max) st->seen_max = raw12;

        int32_t pos = position(st, def->invert);
        if (st->sent >= 0) {
            int32_t center = st->sent * 128 + 64;
            int32_t distance = pos > center ? pos - center : center - pos;
            if (distance <= 64 + POT_HYSTERESIS) continue;
        }
        st->sent = (int16_t)(pos >> 7);
        midi_cc(layout_channel(def->channel), def->cc, (uint8_t)st->sent);
    }
}

void pots_print(void) {
    if (count == 0) printf("no pots in the layout\n");
    for (size_t i = 0; i < count; i++) {
        const pot_state_t *st = &state[i];
        const pot_def_t *def = &layout.pots[i];
        if (!st->enabled) {
            printf("pot %u: skipped (press l)\n", (unsigned)i);
            continue;
        }
        if (st->raw < 0) {
            printf("pot %u  %s  no reading yet\n", (unsigned)i, analog_name(def->adc));
            continue;
        }
        printf("pot %u  %s  raw %4ld/4095  travel %ld-%ld  position %5ld/16383  CC %u = %d\n",
               (unsigned)i, analog_name(def->adc), (long)(st->raw >> 4), (long)(st->lo >> 4),
               (long)(st->hi >> 4), (long)position(st, def->invert), def->cc, st->sent);
    }
}

void pots_calibrate(void) {
    if (count == 0) {
        printf("no pots in the layout\n");
        return;
    }
    if (!calibrating) {
        calibrating = true;
        calibration_restart = true;
        printf("Calibrating: turn every pot from one end to the other, then press c again.\n");
        return;
    }
    calibrating = false;
    printf("Paste into the pots table in src/layout.c:\n");
    for (size_t i = 0; i < count; i++) {
        const pot_state_t *st = &state[i];
        const pot_def_t *def = &layout.pots[i];
        if (!st->enabled) continue;
        printf("    {.adc = %u, .cc = %u", def->adc, def->cc);
        if (def->channel) printf(", .channel = %u", def->channel);
        if (def->invert) printf(", .invert = true");
        printf(", .min = %u, .max = %u},  // %s\n", st->seen_min, st->seen_max, analog_name(def->adc));
        if (st->seen_max - st->seen_min < 2048)
            printf("    ^ pot %u only moved over %u-%u: was it turned end to end?\n", (unsigned)i,
                   st->seen_min, st->seen_max);
    }
}

void pots_describe(void) {
    for (size_t i = 0; i < count; i++)
        if (state[i].enabled)
            printf("H pot %u %u %u\n", layout.pots[i].adc, layout.pots[i].cc, layout_channel(layout.pots[i].channel));
}
