"""Salida de consola y LCD compartida con el firmware Rust (`tests/fixtures/console_v1.json`).

El firmware Rust lee una copia idéntica de la fixture en sus pruebas de host: si un firmware cambia un texto,
una tabla o una fila del LCD sin cambiar la fixture, sus pruebas fallan y los dos dejan de verse igual.
"""

import json
import pathlib
import time

import pytest

import display
import sensors
from conftest import FakeADC
from smartpot_client import BrokerRefused, connect_failure, validate_command
from utils import table_lines

FIXTURE = json.loads(
    (pathlib.Path(__file__).resolve().parent / "fixtures" / "console_v1.json").read_text(encoding="utf-8")
)
MESSAGES = FIXTURE["messages"]


@pytest.mark.parametrize("case", FIXTURE["analogScale"])
def test_analog_scale_matches_the_rust_firmware(case):
    sensor = sensors.ADCSensor(34)
    sensor.scale = (float(case["low"]), float(case["high"]))
    FakeADC.readings = {34: case["raw"]}
    value = sensor.read()
    assert "{:.2f}".format(value) == case["table"]
    decimals = 1 if case["high"] == 14 else 0
    assert ("{:." + str(decimals) + "f}").format(value) == case["lcd"]


def test_reading_table_matches_the_rust_firmware():
    table = FIXTURE["table"]
    assert table_lines(table["readings"], time.gmtime(table["epoch"])) == table["lines"]
    assert table_lines({}, (2000, 1, 1, 0, 0, 0, 5, 1))[1] == table["unsynchronizedHeader"]


def test_lcd_rows_match_the_rust_firmware():
    case = FIXTURE["lcd"]
    lcd = display.LCDDisplay(scl_pin=16, sda_pin=17)
    lcd.show_readings(case["readings"], case["actuators"], online=case["online"])
    icon = display.ONLINE if case["icon"] == "online" else display.OFFLINE
    expected = list(case["rowsWithoutIcon"])
    expected[0] = expected[0] + icon
    assert lcd.lcd.rows == expected


@pytest.mark.parametrize("case", FIXTURE["commandRejections"])
def test_command_rejections_match_the_rust_firmware(case):
    command, command_id, reason = validate_command(case["payload"])
    assert command is None
    assert command_id == case["ackId"]
    assert reason == case["reason"]


@pytest.mark.parametrize("case", FIXTURE["acceptedCommands"])
def test_accepted_commands_match_the_rust_firmware(case):
    command = validate_command(case["payload"])[0]
    assert (command["actuator"], command["action"], command["durationSeconds"]) == (
        case["actuator"], case["action"], case["durationSeconds"])


def test_console_messages_match_the_rust_firmware():
    assert MESSAGES["lcdUnavailable"] == display.UNAVAILABLE
    assert "No connection to broker: " + connect_failure(BrokerRefused(5), "h") == MESSAGES["keyRejected"]
    assert "No connection to broker: " + connect_failure(BrokerRefused(2), "h") == MESSAGES["brokerRefused"]
    main_source = (pathlib.Path(__file__).resolve().parent.parent / "fs" / "main.py").read_text(encoding="utf-8")
    assert '"' + MESSAGES["noWifi"] + '"' in main_source


def test_ca_and_sensor_messages_match_the_rust_firmware(capsys):
    from conftest import FakeDHT22
    from smartpot_client import ca_certificate

    damaged = "-----BEGIN CERTIFICATE-----\nAAAAA\n-----END CERTIFICATE-----"
    for ca, message in (("", "caMissing"), (damaged, "caCorrupted")):
        with pytest.raises(ValueError) as error:
            ca_certificate(ca)
        assert "Cannot connect securely: " + str(error.value) == MESSAGES[message]
    for failure, message in ((True, "dhtNoResponse"), ("checksum", "dhtChecksum")):
        FakeDHT22.fail = failure
        hub = sensors.SensorHub(sensors.AtmosphereSensor(15), {})
        hub.sample(0)
        hub.snapshot(0)
        assert capsys.readouterr().out == MESSAGES[message] + "\n"
    FakeDHT22.fail = False


def test_network_failures_use_the_shared_phrases():
    import errno

    from utils import describe_error

    phrases = set(FIXTURE["networkReasons"])
    for name in ("ETIMEDOUT", "ECONNRESET", "ECONNABORTED", "ECONNREFUSED", "EHOSTUNREACH", "ENOTCONN", "ENOMEM"):
        assert describe_error(OSError(getattr(errno, name))) in phrases, name
    assert describe_error(OSError(-1)) in phrases
    assert describe_error(MemoryError()) in phrases
    assert describe_error(AssertionError()) in phrases
