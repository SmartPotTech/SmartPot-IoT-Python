"""SmartPot firmware for a real crop: ESP32 running MicroPython (Wokwi or physical board)."""

import sys
import time

import machine
import network

import settings
import utils
from actuators import Actuator, ActuatorBank
from device import Device, Supervisor
from display import LCDDisplay, ResilientDisplay
from sensors import AtmosphereSensor, LightSensor, PHSensor, SensorHub, SoilMoistureSensor, TDSSensor
from smartpot_client import SmartPotClient, capabilities_payload

FIRMWARE = "smartpot-iot-python/2.0.0"
POLL_SECONDS = 0.5
# La bienvenida se ve al menos 2 s; después, hasta 30 s de «Connecting WiFi».
WELCOME_SECONDS = 2
WIFI_WAIT_SECONDS = 30
# Tiempo que se muestra el aviso de CA ausente o dañada.
CA_NOTICE_SECONDS = 5
# Periodo del temporizador que revisa actuadores y watchdog aunque el bucle esté bloqueado en la red.
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
        # isconnected() solo es True con dirección IP, igual que el aviso de Rust tras el DHCP.
        up = self.station.isconnected()
        if up and not self.was_up:
            print("SMARTPOT-WIFI-UP ssid={}".format(WIFI["ssid"]))
            print("WiFi connected:", self.station.ifconfig()[0])
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
        except OSError:
            # Un fallo al pedir la conexión se reintenta con la siguiente espera.
            return

    def clock_ready(self):
        return utils.clock_ready()


def sleep_until(clock, deadline):
    while clock.now() < deadline:
        time.sleep(0.1)


def connect_wifi(link, lcd):
    """Boot screen while WiFi associates; if it takes over 30 s the loop keeps measuring and retrying."""
    lcd.show_message("Connecting WiFi", WIFI["ssid"])
    for _ in range(WIFI_WAIT_SECONDS * 4):
        if link.wifi_up():
            return True
        lcd.show_spinner()
        time.sleep(0.25)
    print("No WiFi: device continues measuring and keeps retrying")
    return False


def build_client(settings, lcd, on_command):
    """Without a valid CA the device key is never sent: the device keeps measuring offline."""
    try:
        return SmartPotClient(settings["crop_id"], settings["device_key"], settings["host"], settings["port"],
                              settings["tls"], CONFIG["BROKER"].get("CA_CRT"), on_command,
                              capabilities=capabilities_payload())
    except (ValueError, OSError) as error:
        print("Cannot connect securely:", error)
        lcd.show_message("Check CA in", "config.json")
        time.sleep(CA_NOTICE_SECONDS)
        return None


def start_watchdog(settings):
    seconds = settings.get("watchdog_seconds", 30)
    if not seconds:
        # 0 desactiva el watchdog y el supervisor (por ejemplo, al depurar desde el REPL).
        return None
    return machine.WDT(timeout=int(seconds * 1000))


def main(bank):
    settings = CONFIG["SMARTPOT"]
    utils.set_ntp_server(settings.get("ntp_server"))
    clock = utils.MonotonicClock()
    device = None

    def stalled():
        bank.all_off("watchdog", announce=False)
        print("SMARTPOT-FATAL watchdog task=loop stalled")
        time.sleep(0.05)
        machine.reset()

    supervisor = Supervisor(start_watchdog(settings), lambda: device.beats if device else 0, stalled)
    # Los temporizadores, el failsafe y el watchdog también se revisan mientras el bucle espera a la red:
    # MicroPython ejecuta este callback programado durante las llamadas bloqueantes de lwIP.
    safety_clock = utils.MonotonicClock()

    def safety_tick(_timer):
        now = safety_clock.now()
        bank.tick(now, device.offline_since if device else None)
        supervisor.tick(now)

    safety_timer = machine.Timer(0)
    safety_timer.init(period=SAFETY_TICK_MS, mode=machine.Timer.PERIODIC, callback=safety_tick)

    lcd = ResilientDisplay(lambda: LCDDisplay(scl_pin=16, sda_pin=17))
    lcd.show_message("Welcome to", "SmartPot ESP32")
    welcome_at = clock.now()
    sensors = SensorHub(AtmosphereSensor(15), {
        "brightness": LightSensor(34),
        "ph": PHSensor(35),
        "tds": TDSSensor(32),
        "soilMoisture": SoilMoistureSensor(33),
    })

    # El Wi-Fi empieza a asociarse mientras se ve la bienvenida.
    link = Link()
    link.reconnect_wifi()
    sleep_until(clock, welcome_at + WELCOME_SECONDS)
    keeper = utils.ClockKeeper()
    if connect_wifi(link, lcd):
        keeper.attempt(clock.now())

    client = None

    def on_command(command):
        executed, message = bank.execute(command, clock.now())
        print("Command", command["actuator"], command["action"], "->", message)
        client.acknowledge(command["id"], executed, message)

    client = build_client(settings, lcd, on_command)
    print("SMARTPOT-READY firmware={} reset={}".format(FIRMWARE, machine.reset_cause()))
    device = Device(sensors, bank, lcd, client, link, settings.get("interval_seconds", 30), secure=settings["tls"],
                    report=utils.print_table, reset=machine.reset, clock=keeper, host=settings["host"],
                    heap_report=lambda: utils.heap_report(settings.get("heap_min_free")))
    while True:
        try:
            device.step(clock.now())
        except Exception as error:
            # Un error inesperado no debe dejar un actuador encendido sin control: el ciclo sigue.
            print("Loop error:", error)
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
        bank.all_off("fatal", announce=False)
        print("SMARTPOT-FATAL", type(error).__name__, error)
        sys.print_exception(error)
        time.sleep(1)
        machine.reset()


run()
