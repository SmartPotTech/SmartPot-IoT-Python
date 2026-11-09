import errno
import gc
import random
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
# Hasta la primera sincronización se reintenta cada 10 s; después basta una vez por hora.
FIRST_SYNC_RETRY_SECONDS = 10
RESYNC_SECONDS = 3600

# Frases de error compartidas con el firmware Rust: el mismo fallo se lee igual en las dos consolas.
_NETWORK_ERRORS = (
    ("ETIMEDOUT", "timed out"),
    ("ECONNRESET", "connection reset"),
    ("ECONNABORTED", "connection aborted"),
    ("ECONNREFUSED", "connection refused"),
    ("EHOSTUNREACH", "host unreachable"),
    ("ENOTCONN", "not connected"),
    ("ENOMEM", "out of memory"),
)
NETWORK_ERRORS = {getattr(errno, name): text for name, text in _NETWORK_ERRORS if hasattr(errno, name)}
# getaddrinfo de lwIP falla con estos códigos (EAI_*), con o sin signo según la versión.
DNS_ERRORS = (-2, -3, -200, -201, -202, 200, 201, 202)
# umqtt lanza OSError(-1) cuando el broker cierra el socket.
CLOSED_BY_PEER = -1
DNS_FAILED = "DNS resolution failed"
SNTP_TIMED_OUT = "SNTP request timed out"


def error_code(error):
    args = getattr(error, "args", ())
    return args[0] if args and isinstance(args[0], int) else None


def is_dns_error(error):
    return isinstance(error, OSError) and error_code(error) in DNS_ERRORS


def is_tls_error(error):
    """mbedtls reports its failures as OSError with a negative code below -0x1000."""
    code = error_code(error)
    return isinstance(error, OSError) and code is not None and code <= -0x1000


def describe_error(error):
    """Plain-English reason for a network or MQTT failure, worded like the Rust firmware."""
    if isinstance(error, MemoryError):
        return "out of memory"
    if isinstance(error, AssertionError) or type(error).__name__ == "MQTTException":
        # umqtt valida los paquetes con assert: un paquete inesperado es un error de protocolo.
        return "protocol error"
    code = error_code(error)
    if isinstance(error, OSError) and code is not None:
        if code == CLOSED_BY_PEER:
            return "connection closed by the broker"
        if code in DNS_ERRORS:
            return DNS_FAILED
        if code in NETWORK_ERRORS:
            return NETWORK_ERRORS[code]
        if len(error.args) > 1:
            return str(error.args[1])
    return str(error) or type(error).__name__


def describe_sntp_error(error):
    if error_code(error) in NETWORK_ERRORS and NETWORK_ERRORS[error_code(error)] == "timed out":
        return SNTP_TIMED_OUT
    return describe_error(error)


def clock_ready(moment=None):
    return (moment or time.gmtime())[0] >= SYNCED_YEAR


def set_ntp_server(host):
    """Uses the NTP server of the configuration (`ntp_server`, pool.ntp.org by default)."""
    if host:
        ntptime.host = host


def sync_time():
    """One SNTP request; raises OSError if it fails. TLS needs the current time to validate the broker."""
    ntptime.settime()


class ClockKeeper:
    """Keeps the wall clock synchronized: every 10 s until the first success, then every hour.

    Only the first success and the failures before it reach the console, like the Rust firmware.
    """

    def __init__(self, sync=sync_time):
        self._sync = sync
        self.synced = False
        self.next_attempt = 0

    def due(self, now):
        return now >= self.next_attempt

    def attempt(self, now):
        try:
            self._sync()
        except OSError as error:
            if not self.synced:
                print("Could not synchronize clock:", describe_sntp_error(error))
            self.next_attempt = now + (RESYNC_SECONDS if self.synced else FIRST_SYNC_RETRY_SECONDS)
            return False
        if not self.synced:
            print("Clock synchronized:", time.localtime())
            self.synced = True
        self.next_attempt = now + RESYNC_SECONDS
        return True


class Backoff:
    """Exponential backoff with jitter, the same sequence as `backoff.rs` in the Rust firmware.

    Each failed attempt doubles the ceiling up to `max_ms`; the wait falls in the upper half of the interval
    so that a fleet of devices does not reconnect at the same instant after a broker restart.
    """

    def __init__(self, base_ms=2000, max_ms=60000, random_bits=None):
        self.base_ms = base_ms
        self.max_ms = max_ms
        self.attempt = 0
        self._random = random_bits or (lambda: random.getrandbits(30))

    def reset(self):
        self.attempt = 0

    def next_delay(self):
        """Next wait in seconds; advances the sequence."""
        ceiling = max(1, min(self.base_ms * (1 << min(self.attempt, 16)), self.max_ms))
        self.attempt += 1
        floor = ceiling // 2
        return (floor + self._random() % (ceiling - floor + 1)) / 1000


def timestamp(moment):
    if not clock_ready(moment):
        return "unsynchronized clock"
    return "{}-{:02d}-{:02d} {:02d}:{:02d}:{:02d} UTC".format(*moment[:6])


def _pad(text, width):
    # MicroPython rellena los formatos por bytes: "°" ocupa dos en UTF-8 y la fila salía corta.
    return text + " " * (width - len(text))


def format_row(label, value, unit):
    text = "--" if value is None else "{:.2f} {}".format(value, unit).strip()
    return "| " + _pad(label, 14) + " | " + _pad(text, 14) + " |"


def table_lines(readings, moment=None):
    line = "+" + "-" * 16 + "+" + "-" * 16 + "+"
    lines = [line, "| {:<31} |".format("Reading " + timestamp(moment or time.gmtime())), line]
    for key, label, unit in LABELS:
        lines.append(format_row(label, readings.get(key), unit))
    lines.append(line)
    return lines


def print_table(readings, moment=None):
    # Una sola escritura: ninguna otra línea de la consola puede quedar en medio de la tabla.
    print("\n".join(table_lines(readings, moment)))


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
