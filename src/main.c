// midi-source: a USB MIDI controller for the Raspberry Pi Pico family.
//
// Core 0 runs USB (MIDI and the console) and the LED; core 1 runs the
// instrument (instrument.c), which scans the controls described in layout.c.

#include "console.h"
#include "instrument.h"
#include "led.h"
#include "midi_out.h"
#include "pico/multicore.h"
#include "pico/stdlib.h"
#include "stream.h"
#include "tusb.h"

int main(void) {
    led_init();  // first: on the W boards it boots the radio chip, which takes a while
    midi_init();
    tud_init(0);
    console_init();
    stream_init();

    multicore_launch_core1(instrument_core1_main);

    for (;;) {
        tud_task();
        midi_task();
        console_task();
        stream_task();
        led_task(tud_mounted());
    }
}
