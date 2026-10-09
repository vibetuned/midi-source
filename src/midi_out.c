#include "midi_out.h"

#include <stdio.h>

#include "led.h"
#include "pico/stdlib.h"
#include "pico/util/queue.h"
#include "stream.h"
#include "tusb.h"

// One USB-MIDI event packet (Code Index Number, then the MIDI bytes) and
// when the instrument made it, for the live stream.
typedef struct {
    uint8_t b[4];
    uint32_t t_us;
} midi_packet_t;

// Core 1 -> core 0. Deep enough that a chord or a sweep of every pot never
// overflows while core 0 is busy for a frame.
#define MIDI_QUEUE_LEN 256

static queue_t out_queue;
static volatile uint32_t dropped;  // written by the senders (core 1) only
static uint32_t sent, discarded, received;
static bool monitor;

void midi_init(void) {
    queue_init(&out_queue, sizeof(midi_packet_t), MIDI_QUEUE_LEN);
}

static void send(uint8_t kind, uint8_t channel, uint8_t d1, uint8_t d2) {
    uint8_t status = kind | ((channel - 1) & 0x0F);
    // For channel voice messages the Code Index Number is the status nibble.
    midi_packet_t p = {{(uint8_t)(kind >> 4), status, d1 & 0x7F, d2 & 0x7F}, time_us_32()};
    if (!queue_try_add(&out_queue, &p)) dropped++;
}

void midi_note_on(uint8_t channel, uint8_t note, uint8_t velocity) {
    send(0x90, channel, note, velocity);
}

void midi_note_off(uint8_t channel, uint8_t note, uint8_t velocity) {
    send(0x80, channel, note, velocity);
}

void midi_poly_pressure(uint8_t channel, uint8_t note, uint8_t pressure) {
    send(0xA0, channel, note, pressure);
}

void midi_cc(uint8_t channel, uint8_t controller, uint8_t value) {
    send(0xB0, channel, controller, value);
}

void midi_program_change(uint8_t channel, uint8_t program) {
    send(0xC0, channel, program, 0);
}

void midi_channel_pressure(uint8_t channel, uint8_t pressure) {
    send(0xD0, channel, pressure, 0);
}

void midi_pitch_bend(uint8_t channel, uint16_t value) {
    send(0xE0, channel, value & 0x7F, (value >> 7) & 0x7F);
}

static void print_packet(const char *dir, const midi_packet_t *p) {
    uint8_t status = p->b[1], d1 = p->b[2], d2 = p->b[3];
    unsigned ch = (status & 0x0F) + 1;
    switch (status & 0xF0) {
    case 0x80: printf("%s ch%-2u note off  %3u  vel %3u\n", dir, ch, d1, d2); break;
    case 0x90: printf("%s ch%-2u note on   %3u  vel %3u\n", dir, ch, d1, d2); break;
    case 0xA0: printf("%s ch%-2u poly AT   %3u  %3u\n", dir, ch, d1, d2); break;
    case 0xB0: printf("%s ch%-2u CC %-3u    %3u\n", dir, ch, d1, d2); break;
    case 0xC0: printf("%s ch%-2u program   %3u\n", dir, ch, d1); break;
    case 0xD0: printf("%s ch%-2u pressure  %3u\n", dir, ch, d1); break;
    case 0xE0: printf("%s ch%-2u bend    %5u\n", dir, ch, d1 | (d2 << 7)); break;
    default:
        printf("%s %02x %02x %02x %02x\n", dir, p->b[0], p->b[1], p->b[2], p->b[3]);
        break;
    }
}

void midi_task(void) {
    midi_packet_t p;

    // Outgoing, in order. With no host listening the messages are discarded
    // (the LED still flickers, which helps check wiring on a USB charger).
    // When the USB FIFO is full, the rest wait for the next pass.
    while (queue_try_peek(&out_queue, &p)) {
        if (tud_midi_mounted()) {
            if (!tud_midi_packet_write(p.b)) break;
            sent++;
        } else {
            discarded++;
        }
        queue_try_remove(&out_queue, &p);
        led_activity();
        if (monitor) print_packet("out", &p);
        if (stream_active()) stream_midi(p.t_us, p.b[1], p.b[2], p.b[3]);
    }

    // Incoming: drained so the host never stalls on us; not acted on yet.
    while (tud_midi_packet_read(p.b)) {
        received++;
        led_activity();
        if (monitor) print_packet(" in", &p);
    }
}

midi_stats_t midi_stats(void) {
    return (midi_stats_t){
        .sent = sent, .discarded = discarded, .dropped = dropped, .received = received,
    };
}

void midi_set_monitor(bool on) {
    monitor = on;
}

bool midi_monitor(void) {
    return monitor;
}
