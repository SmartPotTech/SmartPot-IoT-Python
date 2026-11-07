"""SmartPot firmware for a real crop: ESP32 running MicroPython (Wokwi or physical board)."""

import sys
import time

import machine
import network

import settings
import utils
from actuators import Actuator, ActuatorBank
from device import Device
from display import LCDDisplay, ResilientDisplay
from sensors import AtmosphereSensor, LightSensor, PHSensor, SoilMoistureSensor, TDSSensor
from smartpot_client import SmartPotClient, capabilities_payload

FIRMWARE = "smartpot-iot-python/2.0.0"
POLL_SECONDS = 0.5
WIFI_WAIT_SECONDS = 30
# Periodo del temporizador que revisa actuadores aunque el bucle esté bloqueado en la red.
SAFETY_TICK_MS = 500

# Configuración compartida con el firmware Rust (config.json).
CONFIG = settings.load()
WIFI = CONFIG["WIFI"]


class Link:
    """ESP32 WiFi and clock wrapper so Device knows when it can connect to the broker."""

    def __init__(self):
        self.station = network.WLAN(network.STA_IF)
        self.station.active(True)
        self.was_up = False

    def wifi_up(self):
        up = self.station.isconnected()
        if up and not self.was_up:
            print("SMARTPOT-WIFI-UP ssid={}".format(WIFI["ssid"]))
        elif self.was_up and not up:
            print("SMARTPOT-WIFI-DOWN")
        self.was_up = up
        return up

    def reconnect_wifi(self):
        # Llamar a connect() con un intento en curso lo reinicia: solo se pide si la estación está libre.
        if self.station.status() == network.STAT_CONNECTING:
            return
        try:
            self.station.connect(WIFI["ssid"], WIFI["password"])
        except OSError as error:
            print("WiFi:", error)

    def clock_ready(self):
        return utils.clock_ready()

    def sync_time(self):
        return utils.sync_time()


def connect_wifi(link, lcd, watchdog):
    """First attempt on boot with LCD spinner; if it times out, the loop continues measuring and retries."""
    lcd.show_message("Connecting WiFi", WIFI["ssid"])
    link.reconnect_wifi()
    for _ in range(WIFI_WAIT_SECONDS * 4):
        watchdog.feed()
        if link.wifi_up():
            print("WiFi connected:", link.station.ifconfig()[0])
            return True
        lcd.show_spinner()
        time.sleep(0.25)
    print("No WiFi: device continues measuring and retries every 30 s")
    return False


def read_sensors(atmosphere, analog):
    readings = atmosphere.read()
    for name, sensor in analog.items():
        readings[name] = sensor.read()
    return readings


def build_client(settings, lcd, on_command):
    """Without a valid CA the device key is never sent: the device keeps measuring offline."""
    try:
        return SmartPotClient(settings["crop_id"], settings["device_key"], settings["host"], settings["port"],
                              settings["tls"], CONFIG["BROKER"].get("CA_CRT"), on_command,
                              capabilities=capabilities_payload())
    except (ValueError, OSError) as error:
        print("Cannot connect securely:", error)
        lcd.show_message("Check CA in", "config.json")
        time.sleep(5)
        return None


class NoWatchdog:
    """Used when `watchdog_seconds` is 0 (for example, while debugging from the REPL)."""

    def feed(self):
        pass


def start_watchdog(settings):
    seconds = settings.get("watchdog_seconds", 30)
    if not seconds:
        return NoWatchdog()
    return machine.WDT(timeout=int(seconds * 1000))


def main(bank):
    settings = CONFIG["SMARTPOT"]
    utils.set_ntp_server(settings.get("ntp_server"))
    clock = utils.MonotonicClock()
    watchdog = start_watchdog(settings)

    lcd = ResilientDisplay(lambda: LCDDisplay(scl_pin=16, sda_pin=17))
    lcd.show_message("Welcome to", "SmartPot ESP32")
    atmosphere = AtmosphereSensor(15)
    analog = {
        "brightness": LightSensor(34),
        "ph": PHSensor(35),
        "tds": TDSSensor(32),
        "soilMoisture": SoilMoistureSensor(33),
    }

    link = Link()
    if connect_wifi(link, lcd, watchdog):
        watchdog.feed()
        utils.sync_time()

    client = None

    def on_command(command):
        executed, message = bank.execute(command, clock.now())
        print("Command", command["actuator"], command["action"], "->", message)
        client.acknowledge(command["id"], executed, message)

    client = build_client(settings, lcd, on_command)
    device = Device(lambda: read_sensors(atmosphere, analog), bank, lcd, client, link,
                    settings.get("interval_seconds", 30), secure=settings["tls"], report=utils.print_table,
                    reset=machine.reset, heap_report=lambda: utils.heap_report(settings.get("heap_min_free")))

    # Los temporizadores y el failsafe también se revisan mientras el bucle espera a la red: MicroPython
    # ejecuta este callback programado durante las llamadas bloqueantes de lwIP.
    safety_clock = utils.MonotonicClock()
    safety_timer = machine.Timer(0)
    safety_timer.init(period=SAFETY_TICK_MS, mode=machine.Timer.PERIODIC,
                      callback=lambda _timer: bank.tick(safety_clock.now(), device.offline_since))

    print("SMARTPOT-READY firmware={} reset={}".format(FIRMWARE, machine.reset_cause()))
    while True:
        try:
            device.step(clock.now())
        except Exception as error:
            # Un error inesperado no debe dejar un actuador encendido sin control: el ciclo sigue.
            print("Loop error:", error)
        watchdog.feed()
        time.sleep(POLL_SECONDS)


def run():
    # Lo primero es dejar todas las salidas en bajo, antes de la pantalla o la red.
    active_low = CONFIG["SMARTPOT"].get("active_low", False)
    # GPIO5 es un pin de strapping: emite una señal PWM al arrancar; en hardware conviene un pull-down en el relé.
    bank = ActuatorBank({
        "WATER_PUMP": Actuator("Water pump", 19, active_low=active_low),
        "UV_LIGHT": Actuator("UV light", 18, active_low=active_low),
        "FAN": Actuator("Fan", 5, active_low=active_low),
    })
    try:
        main(bank)
    except KeyboardInterrupt:
        # Detención manual desde el REPL: se apagan las salidas y no se reinicia.
        bank.all_off("stopped")
        raise
    except BaseException as error:
        bank.all_off("fatal")
        print("SMARTPOT-FATAL", type(error).__name__, error)
        sys.print_exception(error)
        time.sleep(1)
        machine.reset()


run()
