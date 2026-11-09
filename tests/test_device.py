import errno

from actuators import Actuator, ActuatorBank
from device import LIVENESS_SECONDS, Device, Supervisor
from smartpot_client import BrokerRefused
from utils import Backoff


class FakeLink:
    def __init__(self, wifi=True, clock=True):
        self.wifi, self.clock = wifi, clock
        self.wifi_attempts = 0

    def wifi_up(self):
        return self.wifi

    def reconnect_wifi(self):
        self.wifi_attempts += 1

    def clock_ready(self):
        return self.clock


class FakeKeeper:
    def __init__(self, link, works=True):
        self.link, self.works = link, works
        self.attempts = []

    def due(self, now):
        return not self.attempts or now >= self.attempts[-1] + 10

    def attempt(self, now):
        self.attempts.append(now)
        self.link.clock = self.works
        return self.works


class FakeClient:
    def __init__(self, fail_connect=None):
        self.fail_connect = fail_connect
        self.poll_error = None
        self.connects = 0
        self.closed = 0
        self.published = []

    def connect(self):
        self.connects += 1
        if self.fail_connect:
            raise self.fail_connect

    def poll(self):
        if self.poll_error:
            raise self.poll_error

    def ping(self):
        self.pings = getattr(self, "pings", 0) + 1

    def publish_telemetry(self, readings):
        self.published.append(readings)

    def close(self):
        self.closed += 1


class FakeSensors:
    def sample(self, now):
        pass

    def snapshot(self, now):
        print("TABLE")
        return {"ph": 6.1}


class FakeScreen:
    def __init__(self):
        self.shown = []

    def show_readings(self, readings, states, online):
        self.shown.append((readings, states, online))
        print("LCD", len(self.shown))


def fixed_backoff():
    # Siempre el techo del intervalo: 2 s, 4 s, 8 s...
    return Backoff(random_bits=lambda: -1)


def device(client=None, link=None, clock=None, **options):
    bank = ActuatorBank({"WATER_PUMP": Actuator("Water pump", 19)})
    link = link or FakeLink()
    return Device(FakeSensors(), bank, FakeScreen(), client, link, 30, clock=clock, host="mqtt.smartpot.app",
                  backoff=fixed_backoff(), wifi_backoff=fixed_backoff(), **options)


def test_measures_and_shows_readings_without_a_broker():
    unit = device(client=None)
    unit.step(0)
    assert unit.lcd.shown == [({"ph": 6.1}, {"WATER_PUMP": False}, False)]
    unit.step(10)
    unit.step(30)
    assert len(unit.lcd.shown) == 2


def test_a_timed_actuator_turns_off_while_offline():
    client = FakeClient()
    unit = device(client, FakeLink(wifi=False))
    unit.bank.execute({"actuator": "WATER_PUMP", "action": "ACTIVATE", "durationSeconds": 15}, 0)
    unit.step(0)
    assert unit.bank.states()["WATER_PUMP"]
    unit.step(15)
    assert not unit.bank.states()["WATER_PUMP"]
    assert client.connects == 0


def test_the_first_reading_does_not_wait_for_the_broker_and_a_new_session_starts_with_a_fresh_one(capsys):
    client = FakeClient()
    unit = device(client, heap_report=lambda: print("HEAP"))
    unit.step(0)
    assert capsys.readouterr().out.split() == ["HEAP", "TABLE", "LCD", "1", "TABLE", "LCD", "2"]
    assert unit.online and client.connects == 1
    # Solo la lectura tomada con la sesión abierta se publica.
    assert client.published == [{"ph": 6.1}]
    assert [shown[2] for shown in unit.lcd.shown] == [False, True]


def test_tls_waits_for_the_clock_before_connecting():
    link = FakeLink(clock=False)
    keeper = FakeKeeper(link, works=False)
    client = FakeClient()
    unit = device(client, link, clock=keeper)
    unit.step(0)
    assert client.connects == 0 and keeper.attempts == [0]
    keeper.works = True
    unit.step(9)
    assert keeper.attempts == [0] and client.connects == 0
    unit.step(10)
    assert unit.online and client.connects == 1


def test_plain_mqtt_does_not_need_the_clock():
    unit = device(FakeClient(), FakeLink(clock=False), secure=False)
    unit.step(0)
    assert unit.online


def test_a_lost_connection_is_retried_with_a_short_wait_and_reported_in_words(capsys):
    client = FakeClient()
    unit = device(client)
    unit.step(0)
    client.poll_error = OSError(errno.ECONNRESET)
    unit.step(1)
    assert "Connection lost: connection reset\nSMARTPOT-MQTT-DOWN" in capsys.readouterr().out
    assert not unit.online and client.closed == 1
    client.poll_error = None
    unit.step(2)
    assert client.connects == 1
    unit.step(3)
    assert unit.online and client.connects == 2


def test_failed_connections_back_off_exponentially_and_free_the_socket():
    client = FakeClient(fail_connect=OSError(errno.ETIMEDOUT))
    unit = device(client)
    unit.step(0)
    assert not unit.online and client.closed == 1
    unit.step(1.9)
    assert client.connects == 1
    unit.step(2)
    unit.step(5.9)
    assert client.connects == 2
    unit.step(6)
    assert client.connects == 3


def test_a_rejected_key_is_reported_before_its_marker(capsys):
    unit = device(FakeClient(fail_connect=BrokerRefused(5)))
    unit.step(0)
    out = capsys.readouterr().out
    assert "No connection to broker: broker rejected the device key\nSMARTPOT-MQTT-REFUSED" in out


def test_other_refusals_have_no_refused_marker(capsys):
    unit = device(FakeClient(fail_connect=BrokerRefused(3)))
    unit.step(0)
    out = capsys.readouterr().out
    assert "No connection to broker: broker refused the connection" in out
    assert "SMARTPOT-MQTT-REFUSED" not in out


def test_wifi_is_retried_with_backoff():
    link = FakeLink(wifi=False)
    unit = device(FakeClient(), link)
    unit.step(0)
    assert link.wifi_attempts == 1
    unit.step(1.9)
    assert link.wifi_attempts == 1
    unit.step(2)
    assert link.wifi_attempts == 2
    unit.step(6)
    assert link.wifi_attempts == 3


def test_the_lcd_follows_actuators_and_the_broker_without_waiting_for_the_next_reading():
    client = FakeClient()
    unit = device(client)
    unit.step(0)
    rendered = len(unit.lcd.shown)
    unit.bank.execute({"actuator": "WATER_PUMP", "action": "ACTIVATE", "durationSeconds": 60}, 1)
    unit.step(1)
    assert len(unit.lcd.shown) == rendered + 1 and unit.lcd.shown[-1][1] == {"WATER_PUMP": True}
    unit.step(1.5)
    assert len(unit.lcd.shown) == rendered + 1
    client.poll_error = OSError(errno.ECONNRESET)
    unit.step(2)
    assert unit.lcd.shown[-1][2] is False


def test_the_supervisor_feeds_the_watchdog_while_the_loop_beats():
    class Watchdog:
        feeds = 0

        def feed(self):
            Watchdog.feeds += 1

    beats = [0]
    stalls = []
    supervisor = Supervisor(Watchdog(), lambda: beats[0], lambda: stalls.append(True))
    supervisor.tick(0)
    beats[0] = 1
    supervisor.tick(LIVENESS_SECONDS - 1)
    supervisor.tick(2 * LIVENESS_SECONDS - 2)
    assert Watchdog.feeds == 3 and not stalls
    supervisor.tick(2 * LIVENESS_SECONDS - 1)
    assert stalls == [True] and Watchdog.feeds == 3


def test_without_a_watchdog_the_supervisor_does_nothing():
    stalls = []
    supervisor = Supervisor(None, lambda: 0, lambda: stalls.append(True))
    supervisor.tick(0)
    supervisor.tick(10 * LIVENESS_SECONDS)
    assert not stalls
