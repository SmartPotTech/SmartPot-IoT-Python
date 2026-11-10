"""Imagen de flash para Wokwi: MicroPython con fs/ y config.json en su LittleFS."""

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_flash  # noqa: E402
from littlefs import UserContext  # noqa: E402


def source(tmp_path, config=True):
    folder = tmp_path / "fs"
    folder.mkdir()
    # Bytes tal cual: en Windows write_text cambiaría el salto de línea.
    (folder / "main.py").write_bytes(b"print('hello')\n")
    (folder / "config.example.json").write_text("{}", encoding="utf-8")
    if config:
        (folder / "config.json").write_text(json.dumps({"WIFI": {}, "SMARTPOT": {}, "BROKER": {}}), encoding="utf-8")
    return folder


def test_the_vfs_partition_is_read_from_the_micropython_image():
    firmware = build_flash.FIRMWARE.read_bytes()
    assert build_flash.find_partition(firmware, "vfs") == (0x200000, 0x200000)


def test_the_image_carries_the_modules_and_config_but_not_the_template(tmp_path):
    output = tmp_path / "build"
    build_flash.main(["--source", str(source(tmp_path)), "--output", str(output)])

    flasher_args = json.loads((output / "flasher_args.json").read_text(encoding="utf-8"))
    assert flasher_args["flash_settings"]["flash_size"] == "4MB"
    files = flasher_args["flash_files"]
    assert files["0x200000"] == "littlefs.bin"
    assert (output / files["0x1000"]).resolve() == build_flash.FIRMWARE.resolve()

    image = (output / "littlefs.bin").read_bytes()
    assert len(image) == 0x200000
    fs = build_flash.filesystem(len(image), UserContext(buffer=bytearray(image)))
    assert sorted(fs.listdir("/")) == ["config.json", "main.py"]
    with fs.open("main.py", "rb") as handle:
        assert handle.read() == b"print('hello')\n"


def test_without_config_json_the_image_is_not_built(tmp_path):
    with pytest.raises(SystemExit, match="Missing fs/config.json"):
        build_flash.main(["--source", str(source(tmp_path, config=False)), "--output", str(tmp_path / "build")])
