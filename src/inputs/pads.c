#include "inputs/pads.h"

#include <math.h>
#include <stdio.h>

#include "analog.h"
#include "capture.h"
#include "layout.h"
#include "midi_out.h"
#include "pico/stdlib.h"
#include "stream.h"

// A hit, sample by sample (125 kS/s per input with three of the Pico's in use):
//   idle  - waiting for a sample above the threshold;
//   scan  - the peak is searched for PAD_SCAN_US, then the note on goes out
//           with a velocity from that peak;
//   mask  - the piezo rings for a while; nothing retriggers for PAD_MASK_US,
//           then the note off goes out.
// After the mask, the threshold starts at half the hit's peak and falls back
// to normal over PAD_DECAY_US, so ringing can't retrigger but a fast second
// hit still does.
//
// Levels count from the pad's resting level, tracked while it is quiet: near 0
// for the plain circuit, about 1 V for a biased one (pulled up to ADC_VREF,
// so the piezo's swings below rest are measured too).
#define PAD_REST_SHIFT 6  // rest += (scan mean - rest) / 64: follows drift over about 64 ms
// A hit is back near rest within a few milliseconds. A signal that stays above
// rest + threshold for this many scans means the rest itself moved (a bias
// resistor added while running, say): the rest is learned again from there.
#define PAD_RELEARN_SCANS 50
// At power-up a biased pin charges up to its rest through the bias resistor
// and the piezo (tens of ms): the pad only learns its rest for this many scans.
#define PAD_SETTLE_SCANS 200
#define PAD_SCAN_US   2000
#define PAD_MASK_US   50000   // a stick bouncing on a practice pad strikes again 30-50 ms later
#define PAD_DECAY_US  100000
#define PAD_RETRIGGER_PERCENT 50

// A wired plain pad rests near 0 (the 1 MΩ drains the piezo) and its ringing
// swings back to 0 many times a millisecond. A pin that stays above the
// threshold for this many scans has no piezo circuit on it, just a floating
// input: the pad stays silent until the pin comes back down. (Not checked for
// biased pads, which rest well above 0 by design.)
#define PAD_UNPLUGGED_SCANS 20

// Above the resting level, raw 12-bit. A resting pad behind the block's
// circuit (100 kΩ load) peaks at about 45 above rest; a soft hit on an Evans
// practice pad at about 220.
#define PAD_DEFAULT_THRESHOLD 150
#define PAD_DEFAULT_MAX       4095
#define PAD_RELEASE_VELOCITY  64

#define MAX_PADS 11  // GP26-GP28 and an MCP3008's eight channels
#define RECENT_HITS 16  // peaks kept for the console

typedef enum { PAD_IDLE, PAD_SCAN, PAD_MASK } pad_phase_t;

typedef struct {
    bool enabled;
    uint32_t scan_samples, mask_samples, decay_samples;  // at its input's sample rate
    pad_phase_t phase;
    uint32_t phase_end;     // sample index where scan or mask ends
    uint32_t decay_from;    // sample index where the raised threshold starts falling
    uint16_t decay_level;   // the raised threshold at decay_from, above rest
    uint16_t threshold;     // resolved from the layout, above rest
    uint16_t max;           // above rest
    uint16_t peak;          // raw
    int32_t rest16;         // resting level, 16x
    bool rest_known;
    uint16_t raised_scans;  // consecutive scans that never came down to rest + threshold
    uint16_t settling;      // scans left before the pad may play
    bool unplugged;
    uint16_t high_scans;    // consecutive scans whose lowest sample was above the threshold
    uint16_t resting;       // lowest sample of the latest scan
    // For the console.
    uint32_t hits;
    uint16_t last_peak;            // above rest, like everything shown
    uint8_t last_velocity;
    uint16_t loudest;
    uint16_t recent[RECENT_HITS];  // peaks above rest, oldest overwritten first
} pad_state_t;

static pad_state_t state[MAX_PADS];

// The recorder (console w): once armed, the next scan where a pad goes above
// CAPTURE_LEVEL starts recording that pad's highest sample of every scan
// (1 ms) for CAPTURE_MS. Below the usual thresholds, so it shows hits that
// don't play too, but above a resting pad's noise (peaks of about 65 with a
// 100 kΩ load on the piezo).
#define CAPTURE_MS 200
#define CAPTURE_LEVEL 150
typedef enum { CAPTURE_OFF, CAPTURE_ARMED, CAPTURE_RUNNING, CAPTURE_DONE } capture_phase_t;
static volatile capture_phase_t capture_phase;  // armed and reset by core 0, advanced by core 1
static uint16_t capture[CAPTURE_MS];
static uint32_t capture_len;
static size_t capture_pad;
static size_t count;

static uint32_t samples_for(uint32_t us, uint32_t rate) {
    return (uint32_t)((uint64_t)us * rate / 1000000);
}

void pads_init(void) {
    count = MIN(layout.pad_count, MAX_PADS);
    for (size_t i = 0; i < count; i++) {
        if (!layout_pad_ok(i)) continue;
        const pad_def_t *def = &layout.pads[i];
        pad_state_t *st = &state[i];
        uint32_t rate = analog_rate(def->adc);
        *st = (pad_state_t){
            .enabled = true,
            .scan_samples = samples_for(PAD_SCAN_US, rate),
            .mask_samples = samples_for(PAD_MASK_US, rate),
            .decay_samples = samples_for(PAD_DECAY_US, rate),
            .threshold = def->threshold ? def->threshold : PAD_DEFAULT_THRESHOLD,
            .max = def->max ? def->max : PAD_DEFAULT_MAX,
            .settling = PAD_SETTLE_SCANS,
        };
        if (st->max <= st->threshold) st->max = PAD_DEFAULT_MAX;
    }
}

static int32_t rest(const pad_state_t *st) {
    return st->rest16 >> 4;
}

// The peak of the current hit, above rest.
static int32_t height(const pad_state_t *st) {
    int32_t h = (int32_t)st->peak - rest(st);
    return h < 0 ? 0 : h;
}

// The raw level a sample must exceed to start a hit.
static int32_t threshold_at(const pad_state_t *st, uint32_t index) {
    uint32_t elapsed = index - st->decay_from;
    if (elapsed >= st->decay_samples || st->decay_level <= st->threshold) return rest(st) + st->threshold;
    uint32_t extra = (uint32_t)(st->decay_level - st->threshold) * (st->decay_samples - elapsed) / st->decay_samples;
    return rest(st) + st->threshold + (int32_t)extra;
}

// Square-root curve: a hit's peak grows much faster than how hard it feels,
// so a linear map leaves soft and medium hits crowded at the bottom.
static uint8_t velocity(const pad_state_t *st) {
    float x = (float)(height(st) - st->threshold) / (float)(st->max - st->threshold);
    int32_t v = 1 + (int32_t)(126.0f * sqrtf(x < 0 ? 0 : x > 1 ? 1 : x) + 0.5f);
    return (uint8_t)(v > 127 ? 127 : v);
}

static void run(size_t pad, const pad_def_t *def, pad_state_t *st, const analog_block_t *b, uint32_t now_us) {
    uint8_t ch = layout_channel(def->channel);
    for (size_t k = 0; k < b->count; k++) {
        uint32_t index = b->first + (uint32_t)k;
        uint16_t s = b->samples[k];
        switch (st->phase) {
        case PAD_IDLE:
            if (s > threshold_at(st, index)) {
                capture_trigger(def->adc, index, (uint8_t)pad);
                st->phase = PAD_SCAN;
                st->phase_end = index + st->scan_samples;
                st->peak = s;
            }
            break;
        case PAD_SCAN:
            if (s > st->peak) st->peak = s;
            if ((int32_t)(index - st->phase_end) >= 0) {
                uint8_t v = velocity(st);
                uint16_t h = (uint16_t)height(st);
                midi_note_on(ch, def->note, v);
                stream_hit(now_us, def->adc, st->peak, v);  // raw, where the scope draws it
                st->recent[st->hits % RECENT_HITS] = h;
                st->hits++;
                st->last_peak = h;
                st->last_velocity = v;
                if (h > st->loudest) st->loudest = h;
                st->phase = PAD_MASK;
                st->phase_end = index + st->mask_samples;
            }
            break;
        case PAD_MASK:
            if ((int32_t)(index - st->phase_end) >= 0) {
                midi_note_off(ch, def->note, PAD_RELEASE_VELOCITY);
                st->phase = PAD_IDLE;
                st->decay_from = index;
                st->decay_level = (uint16_t)(height(st) * PAD_RETRIGGER_PERCENT / 100);
            }
            break;
        }
    }
}

// Follow the resting level while the pad is quiet: idle, and nothing in this
// scan reaching halfway to the threshold. Learn it again if it moved for good.
static void track_rest(pad_state_t *st, const analog_block_t *b) {
    if (b->count == 0) return;
    uint32_t sum = 0;
    uint16_t highest = 0, lowest = 4095;
    for (size_t k = 0; k < b->count; k++) {
        uint16_t s = b->samples[k];
        sum += s;
        if (s > highest) highest = s;
        if (s < lowest) lowest = s;
    }
    int32_t mean16 = (int32_t)((sum << 4) / b->count);
    if (!st->rest_known || st->settling) {
        st->rest16 = mean16;
        st->rest_known = true;
        if (st->settling) st->settling--;
        return;
    }
    if (lowest > rest(st) + st->threshold) {
        if (++st->raised_scans >= PAD_RELEARN_SCANS) {
            st->rest16 = mean16;
            st->raised_scans = 0;
        }
        return;
    }
    st->raised_scans = 0;
    if (st->phase == PAD_IDLE && highest <= rest(st) + st->threshold / 2)
        st->rest16 += (mean16 - st->rest16) >> PAD_REST_SHIFT;
}

static void record(size_t pad, const pad_state_t *st, const analog_block_t *b) {
    if (b->count == 0) return;
    uint16_t highest = 0;
    for (size_t k = 0; k < b->count; k++)
        if (b->samples[k] > highest) highest = b->samples[k];

    if (capture_phase == CAPTURE_ARMED && highest > rest(st) + CAPTURE_LEVEL) {
        capture_pad = pad;
        capture_len = 0;
        capture_phase = CAPTURE_RUNNING;
    }
    if (capture_phase == CAPTURE_RUNNING && pad == capture_pad) {
        capture[capture_len++] = highest;
        if (capture_len == CAPTURE_MS) capture_phase = CAPTURE_DONE;
    }
}

static void watch_connection(const pad_def_t *def, pad_state_t *st, const analog_block_t *b) {
    if (b->count == 0) return;
    uint16_t lowest = 4095;
    for (size_t k = 0; k < b->count; k++)
        if (b->samples[k] < lowest) lowest = b->samples[k];
    st->resting = lowest;

    if (lowest <= st->threshold) st->high_scans = 0;
    else if (st->high_scans < PAD_UNPLUGGED_SCANS) st->high_scans++;

    bool unplugged = st->high_scans >= PAD_UNPLUGGED_SCANS;
    if (unplugged && !st->unplugged) {
        if (st->phase == PAD_MASK) midi_note_off(layout_channel(def->channel), def->note, PAD_RELEASE_VELOCITY);
        st->phase = PAD_IDLE;
    }
    st->unplugged = unplugged;
}

void pads_scan(uint32_t now_us) {
    for (size_t i = 0; i < count; i++) {
        pad_state_t *st = &state[i];
        if (!st->enabled) continue;
        const pad_def_t *def = &layout.pads[i];
        const analog_block_t *b = analog_block(def->adc);
        if (!def->biased) {
            watch_connection(def, st, b);
            if (st->unplugged) continue;
        }
        track_rest(st, b);
        if (st->settling) continue;
        record(i, st, b);
        run(i, def, st, b, now_us);
    }
}

void pads_capture_arm(void) {
    if (count == 0) {
        printf("no pads in the layout\n");
        return;
    }
    capture_phase = CAPTURE_ARMED;
    printf("Recording the next hit (%u ms from the first sample above %u)...\n", CAPTURE_MS, CAPTURE_LEVEL);
}

void pads_capture_poll(void) {
    if (capture_phase != CAPTURE_DONE) return;
    const pad_state_t *st = &state[capture_pad];
    printf("pad %u  %s  highest sample of each ms (rest %ld; threshold %u and velocity 127 at %u above it):\n",
           (unsigned)capture_pad, analog_name(layout.pads[capture_pad].adc), (long)rest(st), st->threshold, st->max);
    for (uint32_t row = 0; row < CAPTURE_MS; row += 20) {
        printf("%4lu ms:", (unsigned long)row);
        for (uint32_t k = row; k < row + 20; k++) printf(" %4u", capture[k]);
        printf("\n");
    }
    uint32_t peak_at = 0, last_above = 0;
    for (uint32_t k = 0; k < CAPTURE_MS; k++) {
        if (capture[k] > capture[peak_at]) peak_at = k;
        if (capture[k] > rest(st) + st->threshold) last_above = k;
    }
    printf("peak %u at %lu ms; above the threshold until %lu ms\n", capture[peak_at],
           (unsigned long)peak_at, (unsigned long)last_above);
    capture_phase = CAPTURE_OFF;
}

void pads_print(void) {
    for (size_t i = 0; i < count; i++) {
        const pad_state_t *st = &state[i];
        const pad_def_t *def = &layout.pads[i];
        if (!st->enabled) {
            printf("pad %u: skipped (press l)\n", (unsigned)i);
            continue;
        }
        if (st->unplugged) {
            printf("pad %u  %s  silent: the pin rests at %u, so no piezo circuit is on it "
                   "(a wired pad rests near 0)\n", (unsigned)i, analog_name(def->adc), st->resting);
            continue;
        }
        printf("pad %u  %s  rest %ld%s  %lu hits  last peak %u -> velocity %u  loudest %u  "
               "(above rest: threshold %u, velocity 127 at %u)\n",
               (unsigned)i, analog_name(def->adc), (long)rest(st), def->biased ? " (biased)" : "",
               (unsigned long)st->hits, st->last_peak, st->last_velocity, st->loudest, st->threshold, st->max);
        uint32_t shown = MIN(st->hits, RECENT_HITS);
        if (shown == 0) continue;
        printf("       last %lu peaks, oldest first:", (unsigned long)shown);
        for (uint32_t k = st->hits - shown; k != st->hits; k++) printf(" %u", st->recent[k % RECENT_HITS]);
        printf("\n");
    }
}

void pads_describe(void) {
    for (size_t i = 0; i < count; i++) {
        const pad_def_t *def = &layout.pads[i];
        if (state[i].enabled)
            printf("H pad %u %u %u %u %u %u\n", def->adc, def->note, layout_channel(def->channel),
                   state[i].threshold, state[i].max, def->biased ? 1 : 0);
    }
}
