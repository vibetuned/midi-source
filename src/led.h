#ifndef LED_H
#define LED_H

#include <stdbool.h>

// The on-board LED, core 0 only. Blinks slowly until a host mounts the device,
// then stays lit; every MIDI message flicks it briefly.
void led_init(void);
void led_task(bool mounted);
void led_activity(void);

#endif
