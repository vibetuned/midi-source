#ifndef INPUTS_BUTTONS_H
#define INPUTS_BUTTONS_H

#include <stdint.h>

// Push buttons from the layout, one per GPIO. Core 1.
void buttons_init(void);
void buttons_scan(uint32_t now_us);

#endif
