#ifndef INPUTS_POTS_H
#define INPUTS_POTS_H

#include <stdint.h>

// Potentiometers and faders from the layout, on the ADC pins. Core 1.
void pots_init(void);
void pots_scan(uint32_t now_us);

// Core 0: one line per pot with its raw reading and CC value (console p).
void pots_print(void);

// Core 0, console c: the first call starts measuring each pot's travel, the
// second prints layout lines with the measured .min / .max.
void pots_calibrate(void);

// Core 0: "H pot <adc> <cc> <channel>" per pot, for the live stream's header.
void pots_describe(void);

#endif
