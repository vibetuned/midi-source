#ifndef STREAM_H
#define STREAM_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// The live stream for tools/scope.py, on the console: V starts it, v stops it
// (so does closing the port). After a header of "H ..." lines describing the
// instrument it sends, one per line:
//   A <t_us> <lo> <hi> <mean> ...  every scan (1 ms): each analog input's lowest,
//                                  highest and mean sample, in "H inputs" order
//   M <t_us> <status> <d1> <d2>    every MIDI message the instrument makes
//   P <t_us> <adc> <peak> <vel>    every pad hit: its raw peak and the velocity sent
//   L <t_us> <x_mm> <y_mm> <zone> <vel> <level>...
//                                  every zone pad strike: where it landed, its
//                                  zone (inputs/zones.h), velocity, and the signed
//                                  level of each of its inputs ("H zones" order)
// Times are the board's microsecond clock (wrapping at 2^32). Frames the USB
// link can't take are dropped and counted, never waited for.

void stream_init(void);   // core 0, before core 1 starts
void stream_start(void);  // core 0
void stream_stop(void);   // core 0
bool stream_active(void);

void stream_capture(uint32_t t_us);  // core 1, after every analog scan
void stream_task(void);              // core 0 main loop
void stream_midi(uint32_t t_us, uint8_t status, uint8_t d1, uint8_t d2);  // core 0
void stream_hit(uint32_t t_us, uint8_t adc, uint16_t peak, uint8_t velocity);  // core 1
void stream_strike(uint32_t t_us, float x_cm, float y_cm, uint8_t zone, uint8_t velocity,
                   const int32_t *level, size_t levels);  // core 1

uint32_t stream_dropped(void);

#endif
