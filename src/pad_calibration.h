// Generated from build/calibration/20261009-173826 (tools/scope.py's Pad tab, or
// tools/calibrate.py). Don't edit it: calibrate again, then build and flash.
#ifndef PAD_CALIBRATION_H
#define PAD_CALIBRATION_H

// The zone pad's piezos in the calibration's order: top-left CH0, top-right CH2, bottom CH1.
#define PAD_CALIBRATION_PIEZOS 3
#define PAD_CALIBRATION_ADC {MCP3008_CH(0), MCP3008_CH(2), MCP3008_CH(1)}

// Signature (each piezo's signed level over their length, in the order above)
// and 1, to (x, y) in cm. Between calibration spots hits land within about
// 2.7 cm, at worst 3.6 cm.
#define PAD_CALIBRATION_MAP {{-2.56441f, 3.45723f}, {3.63194f, 2.39087f}, {0.61049f, -4.26800f}, {-0.83065f, -4.20318f}}

// The centre of the piezos' triangle, cm: the default split between zones.
#define PAD_CENTRE_X 0.00f
#define PAD_CENTRE_Y -3.12f

#define PAD_CALIBRATION_RUN "20261009-173826"

#endif
