"""Device loop: always measures, displays, and turns off timed actuators; publishes and obeys when connected.

Hardware-independent: main.py supplies sensors, actuators, display, MQTT client, link (WiFi) and clock keeper,
and calls step() every half second with a monotonic clock in seconds. The order of the console lines is the
same as in the Rust firmware: readings never wait for the network, and each new broker session starts with a
fresh reading.
"""

import gc

from smartpot_client import connect_failure
from utils import Backoff, describe_error

PING_SECONDS = 30
HEAP_REPORT_SECONDS = 60
# Tras estos fallos seguidos por falta de memoria al conectar, el heap está demasiado fragmentado para TLS.
MAX_MEMORY_FAILURES = 3
# Sin un paso del bucle durante este tiempo, el supervisor apaga las salidas y reinicia (igual que en Rust).
LIVENESS_SECONDS = 120


class Supervisor:
    """Feeds the hardware watchdog only while the device loop keeps beating, like `supervisor.rs` in Rust.

    `tick` runs from a timer callback, which MicroPython also executes during blocking network calls. If the loop
    stops beating for LIVENESS_SECONDS, `on_stall` turns the outputs off and restarts with a message; if the whole
    interpreter hangs, the timer stops too and the hardware watchdog restarts the chip. Without a watchdog
    (`watchdog_seconds` = 0, for debugging) neither mechanism runs.
    """

    def __init__(self, watchdog, beats, on_stall, liveness_seconds=LIVENESS_SECONDS):
        self.watchdog = watchdog
        self.beats = beats
        self.on_stall = on_stall
        self.liveness = liveness_seconds
        self.last_count = None
        self.last_change = 0

    def tick(self, now):
        if self.watchdog is None:
            return
        count = self.beats()
        if count != self.last_count:
            self.last_count = count
            self.last_change = now
        elif now - self.last_change >= self.liveness:
            self.on_stall()
            return
        self.watchdog.feed()


class Device:
    def __init__(self, sensors, bank, lcd, client, link, interval_seconds, secure=True, report=None,
                 reset=None, heap_report=None, clock=None, host="", backoff=None, wifi_backoff=None):
        self.sensors = sensors
        self.bank = bank
        self.lcd = lcd
        self.client = client
        self.link = link
        self.interval = interval_seconds
        # Con TLS hace falta la hora sincronizada para validar el certificado del broker.
        self.secure = secure
        self.report = report
        self.reset = reset
        self.heap_report = heap_report
        self.clock = clock
        self.host = host
        self.backoff = backoff or Backoff()
        self.wifi_backoff = wifi_backoff or Backoff()
        self.online = False
        self.offline_since = None
        self.next_attempt = 0
        self.next_wifi_attempt = 0
        self.next_reading = 0
        self.next_ping = 0
        self.next_heap = 0
        self.memory_failures = 0
        self.readings = None
        # Lo último que se dibujó: estados de actuadores y conexión.
        self.shown = None
        # El supervisor de main.py comprueba que el bucle sigue latiendo.
        self.beats = 0

    def step(self, now):
        self.beats += 1
        wifi = self.link.wifi_up()
        if self.online:
            self.offline_since = None
        elif self.offline_since is None:
            self.offline_since = now
        self.bank.tick(now, self.offline_since)
        self.sensors.sample(now)
        if self.heap_report and now >= self.next_heap:
            self.next_heap = now + HEAP_REPORT_SECONDS
            self.heap_report()
        if now >= self.next_reading:
            self._measure(now)
        if wifi:
            self.wifi_backoff.reset()
            if self.clock and self.clock.due(now):
                self.clock.attempt(now)
        elif now >= self.next_wifi_attempt:
            self.next_wifi_attempt = now + self.wifi_backoff.next_delay()
            self.link.reconnect_wifi()
        if not self.online and wifi:
            self._reconnect(now)
        if self.online:
            try:
                self.client.poll()
                if now >= self.next_ping:
                    self.next_ping = now + PING_SECONDS
                    self.client.ping()
            except Exception as error:
                # Cualquier fallo del cliente (también un paquete corrupto) deja el socket desincronizado.
                self._lost(error, now)
        self._refresh_display(now)

    def _reconnect(self, now):
        if self.client is None or now < self.next_attempt:
            return
        if self.secure and not self.link.clock_ready():
            return
        # TLS necesita bloques contiguos grandes: se libera el heap antes del handshake.
        gc.collect()
        try:
            self.client.connect()
        except MemoryError as error:
            self.memory_failures += 1
            print("No memory to connect to the broker:", error)
            self.client.close()
            self.next_attempt = now + self.backoff.next_delay()
            if self.memory_failures >= MAX_MEMORY_FAILURES and self.reset:
                self.bank.all_off("restart", announce=False)
                print("SMARTPOT-FATAL memory: restarting to defragment the heap")
                self.reset()
            return
        except Exception as error:
            print("No connection to broker:", connect_failure(error, self.host))
            if getattr(error, "key_rejected", False):
                print("SMARTPOT-MQTT-REFUSED")
            self.client.close()
            self.next_attempt = now + self.backoff.next_delay()
            return
        self.memory_failures = 0
        self.backoff.reset()
        self.online = True
        self.offline_since = None
        self.next_ping = now + PING_SECONDS
        # Cada sesión nueva empieza con una lectura fresca: la API la recibe sin esperar el intervalo.
        self._measure(now)

    def _measure(self, now):
        self.next_reading = now + self.interval
        readings = self.sensors.snapshot(now)
        self.readings = readings
        if self.report:
            self.report(readings)
        # Se publica antes de tocar la pantalla: un LCD averiado no puede detener la telemetría.
        if self.online:
            try:
                self.client.publish_telemetry(readings)
            except Exception as error:
                self._lost(error, now)
        self._draw()

    def _draw(self):
        states = self.bank.states()
        try:
            self.lcd.show_readings(self.readings, states, self.online)
        except Exception as error:
            print("Display error:", error)
        self.shown = (states, self.online)

    def _refresh_display(self, now):
        """Redraws at once when an actuator or the broker link changes, without waiting for the next reading."""
        tick = getattr(self.lcd, "tick", None)
        if tick:
            tick(now)
        if self.readings is None:
            return
        if getattr(self.lcd, "recovered", False):
            self.lcd.recovered = False
            self._draw()
        elif self.shown != (self.bank.states(), self.online):
            self._draw()

    def _lost(self, error, now):
        print("Connection lost:", describe_error(error))
        print("SMARTPOT-MQTT-DOWN")
        self.online = False
        self.offline_since = now
        self.client.close()
        self.backoff.reset()
        self.next_attempt = now + self.backoff.next_delay()
