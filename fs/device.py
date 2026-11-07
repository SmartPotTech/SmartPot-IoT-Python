"""Device loop: always measures, displays, and turns off timed actuators; publishes and obeys when connected.

Hardware-independent: main.py supplies sensors, actuators, display, MQTT client, and link
(WiFi and clock), and calls step() every half second with a monotonic clock in seconds.
"""

import gc

RETRY_SECONDS = 10
WIFI_RETRY_SECONDS = 30
PING_SECONDS = 30
HEAP_REPORT_SECONDS = 60
# Tras estos fallos seguidos por falta de memoria al conectar, el heap está demasiado fragmentado para TLS.
MAX_MEMORY_FAILURES = 3


class Device:
    def __init__(self, read_sensors, bank, lcd, client, link, interval_seconds, secure=True, report=None,
                 reset=None, heap_report=None):
        self.read_sensors = read_sensors
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
        self.online = False
        self.offline_since = None
        self.next_attempt = 0
        self.next_reading = 0
        self.next_ping = 0
        self.next_heap = 0
        self.memory_failures = 0

    def step(self, now):
        if self.online:
            self.offline_since = None
        elif self.offline_since is None:
            self.offline_since = now
        self.bank.tick(now, self.offline_since)
        if not self.online:
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
        if now >= self.next_reading:
            self.next_reading = now + self.interval
            self._measure(now)
        if self.heap_report and now >= self.next_heap:
            self.next_heap = now + HEAP_REPORT_SECONDS
            self.heap_report()

    def _reconnect(self, now):
        if self.client is None or now < self.next_attempt:
            return
        if not self.link.wifi_up():
            self.next_attempt = now + WIFI_RETRY_SECONDS
            self.link.reconnect_wifi()
            return
        self.next_attempt = now + RETRY_SECONDS
        if self.secure and not self.link.clock_ready() and not self.link.sync_time():
            return
        # TLS necesita bloques contiguos grandes: se libera el heap antes del handshake.
        gc.collect()
        try:
            self.client.connect()
        except MemoryError as error:
            self.memory_failures += 1
            print("No memory to connect to the broker:", error)
            self.client.close()
            if self.memory_failures >= MAX_MEMORY_FAILURES and self.reset:
                self.bank.all_off("restart")
                print("SMARTPOT-FATAL memory: restarting to defragment the heap")
                self.reset()
            return
        except Exception as error:
            print("No connection to broker:", error)
            self.client.close()
            return
        self.memory_failures = 0
        self.online = True
        self.offline_since = None
        self.next_ping = now + PING_SECONDS

    def _measure(self, now):
        readings = self.read_sensors()
        if self.report:
            self.report(readings)
        # Se publica antes de tocar la pantalla: un LCD averiado no puede detener la telemetría.
        if self.online:
            try:
                self.client.publish_telemetry(readings)
            except Exception as error:
                self._lost(error, now)
        try:
            self.lcd.show_readings(readings, self.bank.states(), self.online)
        except Exception as error:
            print("Display error:", error)

    def _lost(self, error, now):
        print("Connection lost:", error)
        print("SMARTPOT-MQTT-DOWN")
        self.online = False
        self.offline_since = now
        self.client.close()
        self.next_attempt = now + RETRY_SECONDS
