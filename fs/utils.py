import gc
import time

import ntptime

LABELS = (
    ("temperature", "Temperature", "°C"),
    ("humidity", "Air humidity", "%"),
    ("brightness", "Light", "lux"),
    ("ph", "pH", ""),
    ("tds", "TDS", "ppm"),
    ("soilMoisture", "Soil moisture", "%"),
)
# El ESP32 arranca en el año 2000: antes de sincronizar por NTP la hora no sirve para validar certificados.
SYNCED_YEAR = 2025


def clock_ready(moment=None):
    return (moment or time.gmtime())[0] >= SYNCED_YEAR


def set_ntp_server(host):
    """Uses the NTP server of the configuration (`ntp_server`, pool.ntp.org by default)."""
    if host:
        ntptime.host = host


def sync_time():
    """Synchronizes the clock via NTP; TLS requires the current time to validate the broker certificate."""
    for _ in range(3):
        try:
            ntptime.settime()
            print("Clock synchronized:", time.localtime())
            return True
        except OSError as error:
            print("Could not synchronize clock:", error)
            time.sleep(2)
    return False


def timestamp(moment):
    if not clock_ready(moment):
        return "unsynchronized clock"
    return "{}-{:02d}-{:02d} {:02d}:{:02d}:{:02d} UTC".format(*moment[:6])


def format_row(label, value, unit):
    text = "--" if value is None else "{:.2f} {}".format(value, unit).strip()
    return "| {:<14} | {:<14} |".format(label, text)


def print_table(readings, moment=None):
    line = "+" + "-" * 16 + "+" + "-" * 16 + "+"
    print(line)
    print("| {:<31} |".format("Reading " + timestamp(moment or time.gmtime())))
    print(line)
    for key, label, unit in LABELS:
        print(format_row(label, readings.get(key), unit))
    print(line)


class MonotonicClock:
    """Monotonic seconds since boot that never jump when NTP sets the wall clock.

    Accumulates `ticks_diff` so the counter of `ticks_ms` can wrap without effect; under CPython (tests) it
    falls back to `time.monotonic`.
    """

    def __init__(self):
        self._ticks = getattr(time, "ticks_ms", None)
        self._diff = getattr(time, "ticks_diff", None)
        self._last = self._read()
        self._elapsed_ms = 0

    def _read(self):
        if self._ticks is None:
            return int(time.monotonic() * 1000)
        return self._ticks()

    def now(self):
        current = self._read()
        delta = self._diff(current, self._last) if self._diff else current - self._last
        self._last = current
        self._elapsed_ms += max(0, delta)
        return self._elapsed_ms / 1000


def heap_report(low_threshold=None):
    """Prints free and used heap; returns the free bytes (None where the runtime has no counters)."""
    free = getattr(gc, "mem_free", None)
    alloc = getattr(gc, "mem_alloc", None)
    if free is None or alloc is None:
        return None
    available = free()
    print("SMARTPOT-HEAP free={} used={}".format(available, alloc()))
    if low_threshold and available < low_threshold:
        print("SMARTPOT-HEAP-LOW free={} threshold={}".format(available, low_threshold))
    return available
