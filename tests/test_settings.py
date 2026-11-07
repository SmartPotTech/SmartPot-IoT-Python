"""Shared configuration loading: config.json first, the older config.py as a fallback."""

import json
import sys
import types

import settings


def test_config_json_is_loaded(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"WIFI": {"ssid": "net", "password": "x"}, "SMARTPOT": {"crop_id": "c"}}))
    loaded = settings.load(str(path))
    assert loaded["WIFI"]["ssid"] == "net"
    assert loaded["SMARTPOT"]["crop_id"] == "c"
    assert loaded["BROKER"] == {}


def test_the_older_config_py_still_works(tmp_path, monkeypatch):
    legacy = types.ModuleType("config")
    legacy.WIFI = {"ssid": "legacy", "password": ""}
    legacy.SMARTPOT = {"crop_id": "old"}
    monkeypatch.setitem(sys.modules, "config", legacy)
    loaded = settings.load(str(tmp_path / "missing.json"))
    assert loaded["WIFI"]["ssid"] == "legacy" and loaded["BROKER"] == {}


def test_the_example_has_every_shared_block():
    import pathlib

    example = pathlib.Path(__file__).resolve().parent.parent / "fs" / "config.example.json"
    loaded = settings.load(str(example))
    assert set(loaded) == {"WIFI", "SMARTPOT", "BROKER"}
    assert loaded["BROKER"]["CA_CRT"].startswith("-----BEGIN CERTIFICATE-----")
    for key in ("crop_id", "device_key", "host", "port", "tls", "interval_seconds", "watchdog_seconds",
                "active_low", "ntp_server"):
        assert key in loaded["SMARTPOT"], key
