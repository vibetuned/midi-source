#ifndef INPUTS_ZONES_H
#define INPUTS_ZONES_H

#include <stdint.h>

// The zone pad from the layout: one pad read through several piezos, each
// strike located and played as the note of the zone it landed in. Core 1.
void zones_init(void);
void zones_scan(uint32_t now_us);

// Core 0: the console's p, and the live stream's header lines
// "H zones <split_x_mm> <split_y_mm> <note x4> <channel> <threshold> <max> locate <input>... extra <input>..."
// (analog inputs: the piezos the map locates with, then the extra ones), then
// "H calibration <run>" if the layout names the calibration its map came from.
void zones_print(void);
void zones_describe(void);

#endif
