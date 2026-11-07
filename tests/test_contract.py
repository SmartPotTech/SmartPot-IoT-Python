"""Contrato MQTT v1 compartido con el firmware Rust y el simulador.

La fixture fija los nombres canónicos de sensores y actuadores, los formatos de
telemetría y el alias heredado. Si el firmware cambia una capacidad sin
actualizar el contrato, estas pruebas fallan.
"""

import json
import pathlib

from smartpot_client import capabilities_payload, parse_command, telemetry_payload, topics

CROP = "66f5a1000000000000000101"
CONTRACT = json.loads(
    (pathlib.Path(__file__).resolve().parent / "fixtures" / "mqtt_v1_contract.json").read_text(encoding="utf-8")
)

# Perfil de hardware de esta placa MicroPython: los dosificadores y el BME280 no están cableados.
INSTALLED_SENSORS = {"temperature", "humidity", "brightness", "ph", "tds", "soilMoisture"}
INSTALLED_ACTUATORS = ["WATER_PUMP", "UV_LIGHT", "FAN"]
ALL_SENSORS = {sensor["key"] for sensor in CONTRACT["sensors"]}
ALL_ACTUATORS = set(CONTRACT["actuators"])


def capabilities():
    return json.loads(capabilities_payload())


def test_capabilities_use_only_contract_sensor_names_and_units():
    payload = capabilities()
    sensors = payload["sensors"]

    assert payload["schema"] == CONTRACT["schema"]
    keys = [sensor["key"] for sensor in sensors]
    assert len(keys) == len(set(keys)), "sensor keys must be unique"
    assert set(keys) == INSTALLED_SENSORS

    units = {sensor["key"]: sensor["unit"] for sensor in CONTRACT["sensors"]}
    for sensor in sensors:
        assert sensor["key"] in ALL_SENSORS, "sensor is not part of the MQTT v1 contract"
        assert sensor["unit"] == units[sensor["key"]], "unit changed for sensor " + sensor["key"]
        assert sensor["resolution"] > 0
        low, high = sensor["range"]
        assert low < high, "sensor " + sensor["key"] + " must declare a valid range"


def test_capabilities_declare_only_canonical_actuator_names():
    actuators = capabilities()["actuators"]

    assert actuators == INSTALLED_ACTUATORS
    for actuator in actuators:
        assert actuator in ALL_ACTUATORS, "actuator is not part of the MQTT v1 contract"
        assert actuator != "LIGHTS", "LIGHTS is an alias, not a capability"


def test_telemetry_formats_are_a_subset_of_the_contract():
    telemetry = capabilities()["device"]["telemetry"]

    assert telemetry["formats"] == ["json-v1"]
    assert set(telemetry["formats"]) <= set(CONTRACT["telemetry"]["formats"])
    # MicroPython solo implementa JSON v1; no debe anunciar el paquete compacto v2.
    assert "compactBytes" not in telemetry


def test_telemetry_keys_are_part_of_the_contract():
    readings = {key: 1.0 for key in ALL_SENSORS}
    payload = json.loads(telemetry_payload(readings))

    assert set(payload) == ALL_SENSORS


def test_contract_aliases_resolve_to_canonical_actuators():
    for alias, canonical in CONTRACT["aliases"].items():
        command = parse_command(json.dumps({"id": "alias-1", "actuator": alias, "action": "ACTIVATE"}))
        assert command is not None
        assert command["actuator"] == canonical, alias + " must resolve to " + canonical
        assert canonical in ALL_ACTUATORS


def test_topics_follow_the_contract_shape():
    resolved = topics(CROP)

    assert resolved == {
        "telemetry": "smartpot/v1/" + CROP + "/telemetry",
        "commands": "smartpot/v1/" + CROP + "/commands",
        "ack": "smartpot/v1/" + CROP + "/commands/ack",
        "status": "smartpot/v1/" + CROP + "/status",
        "capabilities": "smartpot/v1/" + CROP + "/capabilities",
    }
