import json
import pathlib
import ssl

import pytest

from conftest import FakeMQTTClient, LegacyMQTTClient
from smartpot_client import (
    SmartPotClient,
    ack_payload,
    capabilities_payload,
    parse_command,
    telemetry_payload,
    topics,
)

CROP = "66f5a1000000000000000101"
EXAMPLE_CONFIG = pathlib.Path(__file__).resolve().parent.parent / "fs" / "config.example.py"


def example_config():
    settings = {}
    exec(EXAMPLE_CONFIG.read_text(encoding="utf-8"), settings)
    return settings


def client(received=None, **overrides):
    options = {"crop_id": CROP, "device_key": "secret-key", "host": "mqtt.smartpot.app", "port": 8883, "use_tls": False,
               "ca_data": "", "on_command": (received.append if received is not None else lambda c: None),
               "client_factory": FakeMQTTClient}
    options.update(overrides)
    return SmartPotClient(**options)


def test_topics_follow_the_v1_contract():
    assert topics(CROP)["telemetry"] == f"smartpot/v1/{CROP}/telemetry"
    assert topics(CROP)["ack"] == f"smartpot/v1/{CROP}/commands/ack"
    assert topics(CROP)["capabilities"] == f"smartpot/v1/{CROP}/capabilities"


def test_telemetry_skips_missing_sensors_and_rounds():
    payload = json.loads(telemetry_payload({"temperature": 23.456, "ph": 6.1234, "tds": 812.7, "humidity": None}))
    assert payload == {"temperature": 23.5, "ph": 6.12, "tds": 813}


def test_whole_number_telemetry_fields_are_json_integers():
    raw = telemetry_payload({"brightness": 1851.4, "tds": 812.7})
    payload = json.loads(raw)
    assert type(payload["brightness"]) is int
    assert type(payload["tds"]) is int
    assert payload == {"brightness": 1851, "tds": 813}
    assert '"brightness": 1851' in raw and '"tds": 813' in raw
    assert "1851.0" not in raw and "813.0" not in raw


def test_commands_are_parsed_and_invalid_ones_ignored():
    command = parse_command(b'{"id": "c1", "actuator": "water_pump", "action": "activate", "durationSeconds": 15}')
    assert command == {"id": "c1", "actuator": "WATER_PUMP", "action": "ACTIVATE", "durationSeconds": 15}
    assert parse_command(b"not json") is None
    assert parse_command(b'{"actuator": "FAN"}') is None


@pytest.mark.parametrize("command_id", [None, [], ["c1"], {}, {"value": "c1"}, True, False, 0, 7, "", "   "])
def test_commands_with_an_invalid_id_are_rejected(command_id):
    command = {"id": command_id, "actuator": "FAN", "action": "ACTIVATE"}
    assert parse_command(json.dumps(command)) is None


@pytest.mark.parametrize(("field", "value"), [
    ("actuator", None), ("actuator", 7), ("actuator", ["FAN"]), ("actuator", {"id": "FAN"}),
    ("actuator", ""), ("actuator", "   "),
    ("action", None), ("action", 7), ("action", ["ACTIVATE"]), ("action", {"id": "ACTIVATE"}),
    ("action", ""), ("action", "   "),
])
def test_commands_with_an_invalid_actuator_or_action_are_rejected(field, value):
    command = {"id": "c1", "actuator": "FAN", "action": "ACTIVATE"}
    command[field] = value
    assert parse_command(json.dumps(command)) is None


@pytest.mark.parametrize("duration", [-1, 1.5, "15", True, False, [], {}])
def test_commands_with_an_invalid_duration_are_rejected(duration):
    command = {"id": "c1", "actuator": "FAN", "action": "ACTIVATE", "durationSeconds": duration}
    assert parse_command(json.dumps(command)) is None


def test_commands_accept_a_missing_zero_or_positive_duration():
    base = {"id": "c1", "actuator": "FAN", "action": "ACTIVATE"}
    assert parse_command(json.dumps(base))["durationSeconds"] is None
    assert parse_command(json.dumps({**base, "durationSeconds": 0}))["durationSeconds"] == 0
    assert parse_command(json.dumps({**base, "durationSeconds": 15}))["durationSeconds"] == 15


def test_capabilities_payload_follows_the_audit_schema():
    raw = capabilities_payload()
    payload = json.loads(raw)
    assert payload["schema"] == 1
    assert payload["device"] == {
        "firmware": "smartpot-iot-python/2.0.0",
        "hardware": "esp32-devkit-c-v4",
        "telemetry": {"formats": ["json-v1"]},
    }
    sensors = {sensor["key"]: sensor for sensor in payload["sensors"]}
    assert set(sensors) == {"temperature", "humidity", "brightness", "ph", "tds", "soilMoisture"}
    assert "atmosphere" not in sensors
    assert sensors["temperature"]["range"] == [-20, 60]
    assert sensors["humidity"]["range"] == [0, 100]
    assert sensors["brightness"]["range"] == [0, 2000]
    assert sensors["ph"]["range"] == [0, 14]
    assert sensors["tds"]["range"] == [0, 3000]
    assert sensors["soilMoisture"]["range"] == [0, 100]
    assert payload["actuators"] == ["WATER_PUMP", "UV_LIGHT", "FAN"]
    assert "compact-v2" not in payload["device"]["telemetry"]["formats"]
    assert len(raw.encode()) < 2048


def test_acknowledgement_payload():
    assert json.loads(ack_payload("c1", False, "no pump")) == {"id": "c1", "status": "FAILED", "message": "no pump"}


def test_connect_announces_status_with_last_will_and_subscribes():
    smartpot = client()
    smartpot.connect()
    mqtt = smartpot.client
    assert mqtt.client_id == f"smartpot-{CROP}"
    assert mqtt.options["user"] == CROP
    assert mqtt.will == (f"smartpot/v1/{CROP}/status", b"offline", True, 1)
    assert (f"smartpot/v1/{CROP}/status", b"online", True, 1) in mqtt.published
    assert mqtt.subscriptions == [(f"smartpot/v1/{CROP}/commands", 1)]
    assert mqtt.events == [("publish", f"smartpot/v1/{CROP}/status"),
                           ("subscribe", f"smartpot/v1/{CROP}/commands")]


def test_connect_publishes_retained_capabilities_before_subscribing():
    capabilities = capabilities_payload()
    smartpot = client(capabilities=capabilities)
    assert smartpot.capabilities == capabilities.encode()
    smartpot.connect()
    mqtt = smartpot.client
    assert mqtt.events == [("publish", f"smartpot/v1/{CROP}/status"),
                           ("publish", f"smartpot/v1/{CROP}/capabilities"),
                           ("subscribe", f"smartpot/v1/{CROP}/commands")]
    assert mqtt.published[1] == (f"smartpot/v1/{CROP}/capabilities", capabilities.encode(), True, 1)


def test_incoming_commands_reach_the_handler_and_can_be_acknowledged():
    received = []
    smartpot = client(received)
    smartpot.client.pending.append((b"topic", b'{"id": "c9", "actuator": "FAN", "action": "ACTIVATE"}'))
    smartpot.poll()
    assert received[0]["id"] == "c9"

    smartpot.acknowledge("c9", True, "Fan turned on")
    topic, message, _, qos = smartpot.client.published[-1]
    assert topic == f"smartpot/v1/{CROP}/commands/ack"
    assert json.loads(message)["status"] == "EXECUTED"
    assert qos == 1


def test_tls_verifies_the_broker_with_the_ca_of_the_configuration():
    smartpot = client(use_tls=True, ca_data=example_config()["BROKER"]["CA_CRT"])
    context = smartpot.client.options["ssl"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert (("commonName", "SmartPot MQTT CA"),) in context.get_ca_certs()[0]["subject"]


def test_micropython_1_22_receives_the_ca_through_ssl_params():
    ca = example_config()["BROKER"]["CA_CRT"]
    smartpot = client(use_tls=True, ca_data=ca, client_factory=LegacyMQTTClient)
    assert smartpot.client.options["ssl"] is True
    assert smartpot.client.options["ssl_params"] == {
        "server_hostname": "mqtt.smartpot.app", "cert_reqs": ssl.CERT_REQUIRED, "cadata": ssl.PEM_cert_to_DER_cert(ca)}


def test_without_the_ca_the_device_key_is_never_sent():
    for missing in ("", "<SMARTPOT_CA>", None):
        with pytest.raises(ValueError, match="BROKER"):
            client(use_tls=True, ca_data=missing)


def test_a_damaged_ca_stops_the_connection():
    broken = "-----BEGIN CERTIFICATE-----\nnot base64\n-----END CERTIFICATE-----"
    with pytest.raises(ValueError, match="corrupted"):
        client(use_tls=True, ca_data=broken)
    not_a_certificate = "-----BEGIN CERTIFICATE-----\nbm8gZXMgdW4gY2VydGlmaWNhZG8=\n-----END CERTIFICATE-----"
    with pytest.raises(OSError):
        client(use_tls=True, ca_data=not_a_certificate)


def test_plain_mqtt_needs_no_ca():
    assert client(use_tls=False, ca_data="").client.options["ssl"] is None


class MQTTException(Exception):
    """Raised by umqtt.simple when the broker rejects CONNECT."""


class RefusedMQTTClient(FakeMQTTClient):
    def connect(self):
        raise MQTTException(5)


class UnverifiedMQTTClient(FakeMQTTClient):
    def connect(self):
        raise ValueError("\nThe certificate is not correctly signed by the trusted CA\n")


def test_a_refused_key_is_reported_as_a_connection_error():
    smartpot = client(client_factory=RefusedMQTTClient)
    with pytest.raises(OSError, match=r"rejected the device key \(code 5\)"):
        smartpot.connect()


def test_a_broker_that_fails_verification_points_to_the_ca():
    smartpot = client(client_factory=UnverifiedMQTTClient)
    with pytest.raises(OSError, match=r"\(The certificate is not correctly signed by the trusted CA\): check the CA"):
        smartpot.connect()
