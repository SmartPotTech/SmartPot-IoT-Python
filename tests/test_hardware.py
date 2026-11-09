import pytest

from actuators import Actuator, ActuatorBank
from conftest import FakeADC, FakeDHT22
from sensors import (
    AtmosphereSensor,
    LightSensor,
    PHSensor,
    SensorError,
    SensorHub,
    SoilMoistureSensor,
    TDSSensor,
)
from utils import clock_ready, format_row, print_table, timestamp


def bank():
    return ActuatorBank({"WATER_PUMP": Actuator("Water pump", 19), "FAN": Actuator("Fan", 5)})


def test_timed_activation_turns_off_by_itself():
    actuators = bank()
    executed, message = actuators.execute({"actuator": "WATER_PUMP", "action": "ACTIVATE", "durationSeconds": 15}, 100)
    pump = actuators.actuators["WATER_PUMP"]
    assert executed and message == "Water pump turned on for 15 s"
    assert pump.pin.level == 1
    actuators.tick(110)
    assert pump.active
    actuators.tick(115)
    assert not pump.active and pump.pin.level == 0


def test_acknowledgements_read_durations_like_a_person():
    actuators = bank()
    fan = actuators.execute({"actuator": "FAN", "action": "ACTIVATE", "durationSeconds": 600}, 0)
    pump = actuators.execute({"actuator": "WATER_PUMP", "action": "ACTIVATE", "durationSeconds": 7200}, 0)
    assert fan == (True, "Fan turned on for 10 min")
    assert pump == (True, "Water pump turned on for 2 h")


def test_activation_without_duration_stays_on_until_deactivated():
    actuators = bank()
    actuators.execute({"actuator": "FAN", "action": "ACTIVATE", "durationSeconds": None}, 0)
    actuators.tick(10_000)
    assert actuators.states()["FAN"] is True
    assert actuators.execute({"actuator": "FAN", "action": "DEACTIVATE"}, 1) == (True, "Fan turned off")
    assert actuators.states()["FAN"] is False


def test_unknown_actuators_and_actions_fail():
    actuators = bank()
    missing = actuators.execute({"actuator": "HUMIDIFIER", "action": "ACTIVATE"}, 0)
    assert missing == (False, "This device does not have HUMIDIFIER")
    assert actuators.execute({"actuator": "FAN", "action": "BLINK"}, 0)[0] is False


def test_analog_sensors_use_the_exact_12_bit_scale():
    FakeADC.readings = {34: 4095, 35: 2048, 32: 0, 33: 1024}
    assert LightSensor(34).read() == 2000
    assert PHSensor(35).read() == 2048 * 14.0 / 4095
    assert TDSSensor(32).read() == 0
    assert SoilMoistureSensor(33).read() == 1024 * 100.0 / 4095


def test_sensor_failures_have_the_reasons_of_the_rust_driver():
    FakeADC.readings = {34: None}
    assert LightSensor(34).read() is None
    for failure, reason in ((True, "no response"), ("checksum", "checksum mismatch")):
        FakeDHT22.fail = failure
        with pytest.raises(SensorError, match=reason):
            AtmosphereSensor(15).read()
    FakeDHT22.fail = False
    assert AtmosphereSensor(15).read() == (23.5, 61.0)


def test_readings_stay_valid_for_three_sampling_periods(capsys):
    FakeADC.readings = {34: 4095}
    FakeDHT22.fail = False
    hub = SensorHub(AtmosphereSensor(15), {"brightness": LightSensor(34)})
    hub.sample(0)
    FakeDHT22.fail = True
    FakeADC.readings = {34: None}
    for now in (2, 4, 6):
        hub.sample(now)
    assert hub.snapshot(6)["temperature"] == 23.5 and hub.snapshot(6)["brightness"] == 2000
    capsys.readouterr()
    hub.sample(8)
    readings = hub.snapshot(8)
    FakeDHT22.fail = False
    assert readings["temperature"] is None and readings["humidity"] is None and readings["brightness"] is None
    assert capsys.readouterr().out == "Error reading DHT22: no response\n"


def test_console_table_rows():
    assert format_row("pH", 6.1234, "") == "| pH             | 6.12           |"
    assert format_row("Light", None, "lux") == "| Light          | --             |"
    # Mismo ancho que el separador aunque la unidad no sea ASCII.
    assert format_row("Temperature", 24, "°C") == "| Temperature    | 24.00 °C       |"


def test_console_table_tells_when_the_reading_was_taken(capsys):
    print_table({"ph": 6.1}, (2026, 10, 2, 19, 40, 5, 4, 275))
    lines = capsys.readouterr().out.splitlines()
    assert lines[1] == "| Reading 2026-10-02 19:40:05 UTC |"
    assert {len(line) for line in lines} == {35}


def test_the_clock_is_ready_only_after_syncing():
    assert not clock_ready((2000, 1, 1, 0, 0, 12, 5, 1))
    assert timestamp((2000, 1, 1, 0, 0, 12, 5, 1)) == "unsynchronized clock"
    assert clock_ready((2026, 10, 2, 19, 40, 5, 4, 275))
