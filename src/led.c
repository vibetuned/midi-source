#include "led.h"

#include "pico/stdlib.h"

// Pico W and Pico 2 W wire the LED to the wireless chip, not to a GPIO.
#ifdef CYW43_WL_GPIO_LED_PIN
#include "pico/cyw43_arch.h"
#endif

#define LED_ACTIVITY_MS 30

static bool lit;
static absolute_time_t activity_until;

void led_init(void) {
#if defined(PICO_DEFAULT_LED_PIN)
    gpio_init(PICO_DEFAULT_LED_PIN);
    gpio_set_dir(PICO_DEFAULT_LED_PIN, GPIO_OUT);
#elif defined(CYW43_WL_GPIO_LED_PIN)
    cyw43_arch_init();
#endif
}

static void led_put(bool on) {
    if (on == lit) return;  // on the W boards every write is an SPI transaction
    lit = on;
#if defined(PICO_DEFAULT_LED_PIN)
    gpio_put(PICO_DEFAULT_LED_PIN, on);
#elif defined(CYW43_WL_GPIO_LED_PIN)
    cyw43_arch_gpio_put(CYW43_WL_GPIO_LED_PIN, on);
#endif
}

void led_activity(void) {
    activity_until = make_timeout_time_ms(LED_ACTIVITY_MS);
}

void led_task(bool mounted) {
    bool base = mounted || (to_ms_since_boot(get_absolute_time()) / 500) % 2;
    bool active = !time_reached(activity_until);
    led_put(base != active);
}
