#ifndef MIDI_OUT_H
#define MIDI_OUT_H

#include <stdbool.h>
#include <stdint.h>

// Outgoing MIDI. The senders are called by the instrument on core 1; they
// queue the message and midi_task() on core 0 hands it to USB.
//
// Channels are 1-16, as printed on synths. Data values are masked to 7 bits.
void midi_note_on(uint8_t channel, uint8_t note, uint8_t velocity);
void midi_note_off(uint8_t channel, uint8_t note, uint8_t velocity);
void midi_poly_pressure(uint8_t channel, uint8_t note, uint8_t pressure);
void midi_cc(uint8_t channel, uint8_t controller, uint8_t value);
void midi_program_change(uint8_t channel, uint8_t program);
void midi_channel_pressure(uint8_t channel, uint8_t pressure);
void midi_pitch_bend(uint8_t channel, uint16_t value);  // 0-16383, 8192 = center

// Core 0.
void midi_init(void);
void midi_task(void);

typedef struct {
    uint32_t sent;      // handed to USB
    uint32_t discarded; // queued while no host was listening
    uint32_t dropped;   // lost because the queue was full
    uint32_t received;  // from the host
} midi_stats_t;

midi_stats_t midi_stats(void);

// Print every message in and out on the console.
void midi_set_monitor(bool on);
bool midi_monitor(void);

#endif
