#include "inputs/zones.h"

#include <math.h>
#include <stdio.h>

#include "analog.h"
#include "capture.h"
#include "layout.h"
#include "midi_out.h"
#include "pico/stdlib.h"
#include "stream.h"

// A strike, sample by sample on all the pad's piezos:
//   idle     - waiting for any piezo to rise its threshold above its rest;
//   measure  - waiting for the samples up to ZONE_AFTER_US past that crossing.
//              Then each input's signed level over [crossing - ZONE_BEFORE_US,
//              crossing + ZONE_AFTER_US] (the window tools/calibrate.py
//              measures): how far it rose above rest, or fell below it if that
//              swing was larger. Over their length the piezos' levels are the
//              strike's signature; the calibration's map turns it into a
//              place, the place picks the zone, the length gives the velocity.
//              The stream sends every input's level, the extra inputs' too:
//              the scope calibrates from them;
//   mask     - no new strike for ZONE_MASK_US. After it the threshold starts at
//              ZONE_RETRIGGER_PERCENT of the strike's strongest level and falls
//              back over ZONE_DECAY_US: a second stick right after the first
//              still plays, the board's rebound doesn't.
// Each note goes off ZONE_NOTE_US after it started. Everything is timed in
// microseconds (analog_time): the inputs may be on ADCs of different rates.
#define ZONE_BEFORE_US 1500
#define ZONE_AFTER_US 2500
#define ZONE_MASK_US 15000
#define ZONE_DECAY_US 60000
#define ZONE_RETRIGGER_PERCENT 40
#define ZONE_NOTE_US 50000

#define ZONE_DEFAULT_THRESHOLD 150
#define ZONE_DEFAULT_MAX 2400
#define ZONE_RELEASE_VELOCITY 64

// Resting levels, as for single pads (pads.c): followed while quiet, learned
// again after a lasting step, and only learned for the first scans at power-up.
#define ZONE_REST_SHIFT 6
#define ZONE_RELEARN_SCANS 50
#define ZONE_SETTLE_SCANS 200

#define WINDOW_MAX 512  // 4 ms at the Pico ADC's 125 kS/s per input

typedef enum { ZONE_IDLE, ZONE_MEASURE, ZONE_MASK } zone_phase_t;

static struct {
    bool enabled;
    const zone_pad_def_t *def;
    size_t n;                          // inputs: the piezos, then the extra ones
    uint8_t input[ZONE_MAX_INPUTS];
    uint16_t window[ZONE_MAX_INPUTS];  // samples each input's level is measured over
    uint16_t threshold, max;
    int32_t rest16[ZONE_MAX_INPUTS];
    uint16_t raised[ZONE_MAX_INPUTS];
    uint16_t settling;
    zone_phase_t phase;
    uint32_t crossing;     // when the strike first crossed the threshold
    uint32_t phase_end;    // when the mask ends
    uint32_t decay_from;   // when the raised threshold starts falling
    int32_t decay_level;   // raised threshold above rest at decay_from
    bool sounding[ZONE_COUNT];
    uint32_t note_off_at[ZONE_COUNT];
    // For the console.
    uint32_t strikes;
    float last_x, last_y, last_strength;
    int32_t last_levels[ZONE_MAX_INPUTS];
    uint8_t last_zone, last_velocity;
} z;

static uint16_t samples[WINDOW_MAX];

void zones_init(void) {
    if (layout.zone_pad_count == 0 || !layout_zone_pad_ok(0)) return;
    const zone_pad_def_t *def = &layout.zone_pads[0];
    z.def = def;
    z.n = layout_zone_inputs(def, z.input);
    for (size_t k = 0; k < z.n; k++) {
        uint64_t w = (uint64_t)(ZONE_BEFORE_US + ZONE_AFTER_US) * analog_rate(z.input[k]) / 1000000;
        z.window[k] = (uint16_t)MIN(w, WINDOW_MAX);  // 0 if its ADC isn't running: never measured
    }
    z.threshold = def->threshold ? def->threshold : ZONE_DEFAULT_THRESHOLD;
    z.max = def->max ? def->max : ZONE_DEFAULT_MAX;
    if (z.max <= z.threshold) z.max = ZONE_DEFAULT_MAX;
    z.phase_end = z.decay_from = time_us_32();
    z.settling = ZONE_SETTLE_SCANS;
    z.enabled = true;
}

static int32_t rest(size_t k) {
    return z.rest16[k] >> 4;
}

static void track_rest(size_t k, const analog_block_t *b) {
    if (b->count == 0) return;
    uint32_t sum = 0;
    uint16_t highest = 0, lowest = 4095;
    for (size_t i = 0; i < b->count; i++) {
        uint16_t s = b->samples[i];
        sum += s;
        if (s > highest) highest = s;
        if (s < lowest) lowest = s;
    }
    int32_t mean16 = (int32_t)((sum << 4) / b->count);
    if (z.settling) {
        z.rest16[k] = mean16;
        return;
    }
    if (lowest > rest(k) + z.threshold) {
        if (++z.raised[k] >= ZONE_RELEARN_SCANS) {
            z.rest16[k] = mean16;
            z.raised[k] = 0;
        }
        return;
    }
    z.raised[k] = 0;
    if (z.phase == ZONE_IDLE && highest <= rest(k) + z.threshold / 2)
        z.rest16[k] += (mean16 - z.rest16[k]) >> ZONE_REST_SHIFT;
}

// How far above rest a sample must be to start a strike at time t.
static int32_t threshold_at(uint32_t t) {
    uint32_t elapsed = t - z.decay_from;
    if (elapsed >= ZONE_DECAY_US || z.decay_level <= z.threshold) return z.threshold;
    return z.threshold + (int32_t)((uint32_t)(z.decay_level - z.threshold) * (ZONE_DECAY_US - elapsed) / ZONE_DECAY_US);
}

static uint8_t velocity(float strength) {
    float x = (strength - z.threshold) / (float)(z.max - z.threshold);
    int32_t v = 1 + (int32_t)(126.0f * sqrtf(x < 0 ? 0 : x > 1 ? 1 : x) + 0.5f);
    return (uint8_t)(v > 127 ? 127 : v);
}

// The strike's levels, place, zone and note.
static void strike(uint32_t now_us) {
    const zone_pad_def_t *def = z.def;
    int32_t level[ZONE_MAX_INPUTS], strongest = 0;
    float strength2 = 0;
    for (size_t k = 0; k < z.n; k++) {
        level[k] = 0;
        if (!z.window[k]) continue;
        uint32_t first = analog_index(z.input[k], z.crossing - ZONE_BEFORE_US);
        if (!analog_history(z.input[k], first, z.window[k], samples)) {
            if (k < def->piezos) return;  // too late: the samples are gone (a scan overran)
            continue;
        }
        int32_t hi = 0, lo = 4095;
        for (size_t i = 0; i < z.window[k]; i++) {
            if (samples[i] > hi) hi = samples[i];
            if (samples[i] < lo) lo = samples[i];
        }
        int32_t up = hi - rest(k), down = rest(k) - lo;
        level[k] = up >= down ? up : -down;
        if (k < def->piezos) {
            if (level[k] > strongest) strongest = level[k];
            strength2 += (float)level[k] * (float)level[k];
        }
    }
    float strength = sqrtf(strength2);
    if (strength <= 0) return;
    float x = def->map[def->piezos][0], y = def->map[def->piezos][1];
    for (size_t k = 0; k < def->piezos; k++) {
        x += level[k] / strength * def->map[k][0];
        y += level[k] / strength * def->map[k][1];
    }
    uint8_t zone = (uint8_t)((x < def->split_x ? 0 : 1) + (y >= def->split_y ? 0 : 2));
    uint8_t v = velocity(strength);
    uint8_t ch = layout_channel(def->channel);
    if (z.sounding[zone]) midi_note_off(ch, def->notes[zone], ZONE_RELEASE_VELOCITY);  // struck again
    midi_note_on(ch, def->notes[zone], v);
    z.sounding[zone] = true;
    z.note_off_at[zone] = z.crossing + ZONE_NOTE_US;
    stream_strike(now_us, x, y, zone, v, level, z.n);

    z.decay_level = strongest * ZONE_RETRIGGER_PERCENT / 100;
    z.strikes++;
    z.last_x = x;
    z.last_y = y;
    z.last_strength = strength;
    z.last_zone = zone;
    z.last_velocity = v;
    for (size_t k = 0; k < z.n; k++) z.last_levels[k] = level[k];
}

// The earliest sample, on any piezo, past the threshold (and past the mask).
static void find_crossing(const analog_block_t *const *b) {
    bool found = false;
    uint32_t when = 0, index = 0;
    size_t which = 0;
    for (size_t k = 0; k < z.def->piezos; k++) {
        if (!b[k]->count) continue;
        uint32_t t0 = analog_time(z.input[k], b[k]->first), step = analog_period_q16(z.input[k]), since = 0;
        for (size_t i = 0; i < b[k]->count; i++, since += step) {
            uint32_t t = t0 + (since >> 16);
            if (found && (int32_t)(t - when) >= 0) break;  // no earlier than one already found
            if ((int32_t)(t - z.phase_end) < 0) continue;  // still masked
            if ((int32_t)b[k]->samples[i] - rest(k) > threshold_at(t)) {
                found = true;
                when = t;
                index = b[k]->first + (uint32_t)i;
                which = k;
                break;
            }
        }
    }
    if (!found) return;
    z.crossing = when;
    z.phase = ZONE_MEASURE;
    capture_trigger(z.input[which], index, 0);
}

void zones_scan(uint32_t now_us) {
    if (!z.enabled) return;
    const zone_pad_def_t *def = z.def;
    const analog_block_t *b[ZONE_MAX_INPUTS];
    for (size_t k = 0; k < z.n; k++) {
        b[k] = analog_block(z.input[k]);
        track_rest(k, b[k]);
    }
    if (z.settling) {
        z.settling--;
        return;
    }

    uint8_t ch = layout_channel(def->channel);
    for (size_t zone = 0; zone < ZONE_COUNT; zone++) {
        if (z.sounding[zone] && (int32_t)(now_us - z.note_off_at[zone]) >= 0) {
            midi_note_off(ch, def->notes[zone], ZONE_RELEASE_VELOCITY);
            z.sounding[zone] = false;
        }
    }

    // Every input's samples are in up to `ready`.
    uint32_t ready = now_us;
    for (size_t k = 0; k < z.n; k++) {
        if (!b[k]->count) continue;
        uint32_t newest = analog_time(z.input[k], b[k]->first + (uint32_t)b[k]->count - 1);
        if ((int32_t)(newest - ready) < 0) ready = newest;
    }

    if (z.phase == ZONE_MASK && (int32_t)(ready - z.phase_end) >= 0) {
        z.phase = ZONE_IDLE;
        z.decay_from = z.phase_end;
    }
    if (z.phase == ZONE_IDLE) {
        // Long quiet: keep the mask's end and the decay's start recent, or
        // after 35 minutes their microseconds would look like the future.
        if ((int32_t)(ready - z.phase_end) > ZONE_DECAY_US) z.phase_end = z.decay_from = ready - ZONE_DECAY_US;
        find_crossing(b);
    }
    if (z.phase == ZONE_MEASURE && (int32_t)(ready - (z.crossing + ZONE_AFTER_US)) >= 0) {
        strike(now_us);
        z.phase = ZONE_MASK;
        z.phase_end = z.crossing + ZONE_MASK_US;
    }
}

static const char *const ZONE_NAMES[ZONE_COUNT] = {"top-left", "top-right", "bottom-left", "bottom-right"};

void zones_print(void) {
    if (!z.enabled) {
        if (layout.zone_pad_count) printf("zone pad: skipped (press l)\n");
        return;
    }
    const zone_pad_def_t *def = z.def;
    printf("zone pad ");
    for (size_t k = 0; k < z.n; k++)
        printf(" %s%s %ld", k == def->piezos ? "| extra " : "", analog_name(z.input[k]), (long)rest(k));
    printf("  (rest)  %lu strikes  (threshold %u above rest, velocity 127 at strength %u)\n",
           (unsigned long)z.strikes, z.threshold, z.max);
    printf("          zones split at x %.1f, y %.1f cm: notes %u %u %u %u (top-left, top-right, bottom-left, "
           "bottom-right)\n", (double)def->split_x, (double)def->split_y, def->notes[0], def->notes[1],
           def->notes[2], def->notes[3]);
    if (!z.strikes) return;
    printf("          last strike: x %.1f, y %.1f cm -> %s, note %u, velocity %u  (strength %.0f; levels",
           (double)z.last_x, (double)z.last_y, ZONE_NAMES[z.last_zone], def->notes[z.last_zone], z.last_velocity,
           (double)z.last_strength);
    for (size_t k = 0; k < z.n; k++) printf(" %+ld", (long)z.last_levels[k]);
    printf(")\n");
}

void zones_describe(void) {
    if (!z.enabled) return;
    const zone_pad_def_t *def = z.def;
    printf("H zones %d %d %u %u %u %u %u %u %u locate", (int)lroundf(def->split_x * 10),
           (int)lroundf(def->split_y * 10), def->notes[0], def->notes[1], def->notes[2], def->notes[3],
           layout_channel(def->channel), z.threshold, z.max);
    for (size_t k = 0; k < z.n; k++) printf("%s %u", k == def->piezos ? " extra" : "", z.input[k]);
    if (z.n == def->piezos) printf(" extra");
    printf("\n");
    if (def->calibration) printf("H calibration %s\n", def->calibration);
}
