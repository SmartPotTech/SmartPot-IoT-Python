"""Safety behaviour: capped critical actuators, offline failsafe, resilient display and connection recovery."""

from actuators import MAX_DURATION_SECONDS, OFFLINE_CUTOFF_SECONDS, Actuator, ActuatorBank
from conftest import FakeLcd
from device import MAX_MEMORY_FAILURES, PING_SECONDS, Device
from display import LCDDisplay, NullDisplay, ResilientDisplay
from utils import MonotonicClock


def bank():
    return ActuatorBank({
        "WATER_PUMP": Actuator("Water pump", 19),
        "UV_LIGHT": Actuator("UV light", 18),
    })


class Link:
    def wifi_up(self):
        return True

    def reconnect_wifi(self):
        pass

    def clock_ready(self):
        return True

    def sync_time(self):
        return True


class Client:
    def __init__(self):
        self.poll_error = None
        self.connect_error = None
        self.published = []
        self.pings = 0
        self.closed = 0
        self.connects = 0

    def connect(self):
        self.connects += 1
        if self.connect_error:
            raise self.connect_error

    def poll(self):
        if self.poll_error:
            raise self.poll_error

    def ping(self):
        self.pings += 1

    def publish_telemetry(self, readings):
        self.published.append(readings)

    def close(self):
        self.closed += 1


class BrokenScreen:
    def show_readings(self, readings, states, online):
        raise OSError(110, "ETIMEDOUT")


def device(client, screen=None, actuators=None, **options):
    return Device(lambda: {"ph": 6.1}, actuators or bank(), screen or NullDisplay(), client, Link(), 30, **options)


def test_pump_without_duration_gets_the_safety_cap_but_uv_light_does_not():
    actuators = bank()
    ok, message = actuators.execute({"id": "a", "actuator": "WATER_PUMP", "action": "ACTIVATE",
                                     "durationSeconds": None}, 0)
    assert ok and message == "Water pump turned on for 1 h (safety limit)"
    assert actuators.actuators["WATER_PUMP"].until == MAX_DURATION_SECONDS

    ok, message = actuators.execute({"id": "b", "actuator": "UV_LIGHT", "action": "ACTIVATE",
                                     "durationSeconds": None}, 0)
    assert ok and message == "UV light turned on"
    assert actuators.actuators["UV_LIGHT"].until is None


def test_failsafe_turns_only_critical_actuators_off_after_the_offline_window():
    actuators = bank()
    actuators.execute({"actuator": "WATER_PUMP", "action": "ACTIVATE", "durationSeconds": 3600}, 100)
    actuators.execute({"actuator": "UV_LIGHT", "action": "ACTIVATE", "durationSeconds": None}, 100)
    # Sin broker desde el segundo 50: el plazo cuenta desde el encendido (100), que es posterior.
    actuators.tick(100 + OFFLINE_CUTOFF_SECONDS - 1, offline_since=50)
    assert actuators.states()["WATER_PUMP"]
    actuators.tick(100 + OFFLINE_CUTOFF_SECONDS, offline_since=50)
    assert actuators.states() == {"WATER_PUMP": False, "UV_LIGHT": True}


def test_with_a_broker_the_failsafe_never_fires():
    actuators = bank()
    actuators.execute({"actuator": "WATER_PUMP", "action": "ACTIVATE", "durationSeconds": 3600}, 0)
    actuators.tick(3000, offline_since=None)
    assert actuators.states()["WATER_PUMP"]


def test_a_broken_display_does_not_stop_telemetry():
    client = Client()
    unit = device(client, screen=BrokenScreen())
    unit.step(0)
    assert unit.online and client.published == [{"ph": 6.1}]
    unit.step(30)
    assert client.published == [{"ph": 6.1}, {"ph": 6.1}]


def test_any_client_error_counts_as_a_lost_connection():
    client = Client()
    unit = device(client)
    unit.step(0)
    client.poll_error = AssertionError("corrupted packet")
    unit.step(1)
    assert not unit.online and client.closed == 1 and unit.offline_since == 1


def test_the_device_pings_the_broker_periodically():
    client = Client()
    unit = device(client)
    unit.step(0)
    unit.step(PING_SECONDS - 1)
    assert client.pings == 0
    unit.step(PING_SECONDS)
    assert client.pings == 1


def test_repeated_memory_failures_turn_actuators_off_and_restart():
    client = Client()
    client.connect_error = MemoryError("TLS buffers")
    restarts = []
    actuators = bank()
    actuators.execute({"actuator": "WATER_PUMP", "action": "ACTIVATE", "durationSeconds": 60}, 0)
    unit = device(client, actuators=actuators, reset=lambda: restarts.append(True))
    for attempt in range(MAX_MEMORY_FAILURES):
        unit.step(attempt * 10)
    assert restarts == [True]
    assert not actuators.states()["WATER_PUMP"]


def test_device_failsafe_runs_while_the_broker_is_unreachable():
    client = Client()
    client.connect_error = OSError("broker down")
    actuators = bank()
    unit = device(client, actuators=actuators)
    unit.step(0)
    actuators.execute({"actuator": "WATER_PUMP", "action": "ACTIVATE", "durationSeconds": 3600}, 1)
    unit.step(1 + OFFLINE_CUTOFF_SECONDS)
    assert not actuators.states()["WATER_PUMP"]


def test_resilient_display_survives_a_missing_lcd_and_reprobes_it():
    attempts = []

    def factory():
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError(19, "ENODEV")
        return LCDDisplay(scl_pin=16, sda_pin=17)

    screen = ResilientDisplay(factory)
    assert isinstance(screen.display, NullDisplay)
    for _ in range(10):
        screen.show_readings({"ph": 6.1}, {}, online=False)
    assert isinstance(screen.display, LCDDisplay)
    assert isinstance(screen.display.lcd, FakeLcd)


def test_lcd_rows_are_only_rewritten_when_they_change(capsys):
    screen = LCDDisplay(scl_pin=16, sda_pin=17)
    writes = []
    screen.print = lambda text, row, col=0: writes.append(row)
    screen.show_readings({"ph": 6.1}, {}, online=False)
    screen.show_readings({"ph": 6.1}, {}, online=False)
    assert writes == [0, 1, 2, 3]
    assert "SMARTPOT-LCD n=2" in capsys.readouterr().out


def test_monotonic_clock_never_goes_backwards():
    clock = MonotonicClock()
    readings = [clock.now() for _ in range(1000)]
    assert readings == sorted(readings)


def test_active_low_relays_are_off_with_the_pin_high():
    relay = Actuator("Water pump", 19, active_low=True)
    assert relay.pin.value() == 1 and not relay.active
    relay.turn_on(10, 0)
    assert relay.pin.value() == 0
    relay.turn_off()
    assert relay.pin.value() == 1

    standard = Actuator("Fan", 5)
    assert standard.pin.value() == 0
    standard.turn_on(None, 0)
    assert standard.pin.value() == 1
