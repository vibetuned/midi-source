#include "stream.h"

#include <stdio.h>

#include "analog.h"
#include "hardware/sync.h"
#include "inputs/pads.h"
#include "inputs/pots.h"
#include "inputs/zones.h"
#include "layout.h"
#include "pico/util/queue.h"
#include "tusb.h"

typedef struct {
    uint32_t t_us;
    uint16_t lo[ANALOG_MAX_INPUTS], hi[ANALOG_MAX_INPUTS], mean[ANALOG_MAX_INPUTS];
} frame_t;

// Core 1 -> core 0: about 100 ms of frames, and pad hits.
#define FRAME_QUEUE_LEN 100
#define HIT_QUEUE_LEN 16

typedef struct {
    uint32_t t_us;
    uint16_t peak;
    uint8_t adc, velocity;
} hit_t;

typedef struct {
    uint32_t t_us;
    int16_t x_mm, y_mm;
    int16_t level[ZONE_MAX_INPUTS];
    uint8_t levels, zone, velocity;
} strike_t;

static queue_t frames, hits, strikes;
static volatile bool active;
static volatile uint32_t dropped;
static uint8_t inputs[ANALOG_MAX_INPUTS];
static size_t input_count;

void stream_init(void) {
    queue_init(&frames, sizeof(frame_t), FRAME_QUEUE_LEN);
    queue_init(&hits, sizeof(hit_t), HIT_QUEUE_LEN);
    queue_init(&strikes, sizeof(strike_t), HIT_QUEUE_LEN);
}

bool stream_active(void) {
    return active;
}

uint32_t stream_dropped(void) {
    return dropped;
}

void stream_start(void) {
    input_count = analog_inputs(inputs);
    printf("H board %s\n", PICO_BOARD);
    analog_stats_t pico = analog_stats(ANALOG_PICO), mcp = analog_stats(ANALOG_MCP3008);
    printf("H rate %lu\n", (unsigned long)pico.input_rate);
    if (mcp.inputs) printf("H mcp3008 %lu %u\n", (unsigned long)mcp.input_rate, mcp.bits);
    printf("H inputs");
    for (size_t i = 0; i < input_count; i++) printf(" %u", inputs[i]);
    printf("\n");
    pots_describe();
    pads_describe();
    zones_describe();
    printf("H end\n");
    frame_t f;
    while (queue_try_remove(&frames, &f)) {
    }
    hit_t h;
    while (queue_try_remove(&hits, &h)) {
    }
    strike_t s;
    while (queue_try_remove(&strikes, &s)) {
    }
    dropped = 0;
    __dmb();  // core 1 must see the input list before it sees `active`
    active = true;
}

void stream_stop(void) {
    active = false;
}

void stream_capture(uint32_t t_us) {
    if (!active) return;
    frame_t f = {.t_us = t_us};
    for (size_t i = 0; i < input_count; i++) {
        const analog_block_t *b = analog_block(inputs[i]);
        uint16_t lo = 4095, hi = 0;
        uint32_t sum = 0;
        for (size_t k = 0; k < b->count; k++) {
            uint16_t s = b->samples[k];
            if (s < lo) lo = s;
            if (s > hi) hi = s;
            sum += s;
        }
        f.lo[i] = b->count ? lo : 0;
        f.hi[i] = hi;
        f.mean[i] = b->count ? (uint16_t)(sum / b->count) : 0;
    }
    if (!queue_try_add(&frames, &f)) dropped++;
}

void stream_hit(uint32_t t_us, uint8_t adc, uint16_t peak, uint8_t velocity) {
    if (!active) return;
    hit_t h = {.t_us = t_us, .peak = peak, .adc = adc, .velocity = velocity};
    if (!queue_try_add(&hits, &h)) dropped++;
}

void stream_strike(uint32_t t_us, float x_cm, float y_cm, uint8_t zone, uint8_t velocity, const int32_t *level,
                   size_t levels) {
    if (!active) return;
    strike_t s = {.t_us = t_us, .x_mm = (int16_t)(x_cm * 10), .y_mm = (int16_t)(y_cm * 10),
                  .levels = (uint8_t)MIN(levels, ZONE_MAX_INPUTS), .zone = zone, .velocity = velocity};
    for (size_t k = 0; k < s.levels; k++) s.level[k] = (int16_t)level[k];
    if (!queue_try_add(&strikes, &s)) dropped++;
}

// Write a whole line or nothing: a full USB FIFO drops it instead of waiting.
static bool write_line(const char *line, int len) {
    if (len <= 0 || (int)tud_cdc_write_available() < len) return false;
    tud_cdc_write(line, (uint32_t)len);
    return true;
}

void stream_midi(uint32_t t_us, uint8_t status, uint8_t d1, uint8_t d2) {
    char line[40];
    int len = snprintf(line, sizeof line, "M %lu %u %u %u\n", (unsigned long)t_us, status, d1, d2);
    if (!write_line(line, len)) dropped++;
}

void stream_task(void) {
    if (!active) return;
    if (!tud_cdc_connected()) {  // the scope went away
        stream_stop();
        return;
    }
    hit_t h;
    while (queue_try_peek(&hits, &h)) {
        char line[40];
        int len = snprintf(line, sizeof line, "P %lu %u %u %u\n", (unsigned long)h.t_us, h.adc, h.peak, h.velocity);
        if (!write_line(line, len)) break;
        queue_try_remove(&hits, &h);
    }
    strike_t s;
    while (queue_try_peek(&strikes, &s)) {
        char line[128];
        int len = snprintf(line, sizeof line, "L %lu %d %d %u %u", (unsigned long)s.t_us, s.x_mm, s.y_mm, s.zone,
                           s.velocity);
        for (size_t k = 0; k < s.levels; k++) len += snprintf(line + len, sizeof line - (size_t)len, " %d", s.level[k]);
        line[len++] = '\n';
        if (!write_line(line, len)) break;
        queue_try_remove(&strikes, &s);
    }
    frame_t f;
    while (queue_try_peek(&frames, &f)) {
        char line[200];
        int len = snprintf(line, sizeof line, "A %lu", (unsigned long)f.t_us);
        for (size_t i = 0; i < input_count; i++)
            len += snprintf(line + len, sizeof line - (size_t)len, " %u %u %u", f.lo[i], f.hi[i], f.mean[i]);
        line[len++] = '\n';
        if ((int)tud_cdc_write_available() < len) break;  // the rest waits in the queue
        write_line(line, len);
        queue_try_remove(&frames, &f);
    }
    tud_cdc_write_flush();
}
