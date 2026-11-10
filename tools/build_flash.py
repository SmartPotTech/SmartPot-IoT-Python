"""Builds the flash image for the Wokwi simulator: MicroPython with the firmware already on its filesystem.

The simulator loads `build/flasher_args.json` (see wokwi.toml): the official MicroPython image at 0x1000 and a
LittleFS image with `fs/*.py` and `fs/config.json` at the offset of the `vfs` partition. MicroPython mounts that
partition at boot and runs `main.py` by itself, like the Rust firmware with its ELF. Run it again after changing
`fs/`:

    uv run python tools/build_flash.py

`build/` is not versioned: the image carries `config.json`, with the device key and the WiFi password.
"""

import argparse
import json
import os
import pathlib
import struct
import sys

from littlefs import LittleFS, UserContext

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIRMWARE = ROOT / "ESP32_GENERIC-20231227-v1.22.0.bin"
SOURCE = ROOT / "fs"
BUILD = ROOT / "build"
# El binario oficial de MicroPython para ESP32 se graba a partir del cargador de arranque.
FIRMWARE_OFFSET = 0x1000
PARTITION_TABLE_OFFSET = 0x8000
PARTITION_MAGIC = b"\xaa\x50"
FLASH_SIZE = "4MB"
# Misma configuración que `VfsLfs2` de MicroPython (readsize, progsize y lookahead de 32 bytes): así el
# intérprete monta la imagen sin formatearla.
LFS_READ_SIZE = 32
LFS_PROG_SIZE = 32
LFS_LOOKAHEAD = 32
LFS_CACHE_SIZE = 4 * max(LFS_READ_SIZE, LFS_PROG_SIZE)
# Plantilla y caché: no van a la placa.
SKIPPED = {"config.example.json"}


def find_partition(firmware, label):
    """Returns (offset, size) of a partition from the table embedded in the MicroPython image."""
    start = PARTITION_TABLE_OFFSET - FIRMWARE_OFFSET
    for index in range(start, start + 0xC00, 32):
        entry = firmware[index:index + 32]
        if entry[:2] != PARTITION_MAGIC:
            break
        name = entry[12:28].rstrip(b"\0").decode()
        if name == label:
            return struct.unpack("<II", entry[4:12])
    raise SystemExit("The MicroPython image has no '{}' partition".format(label))


def firmware_files(source):
    """Files that go to the board: the modules of fs/ and config.json."""
    config = source / "config.json"
    if not config.exists():
        raise SystemExit("Missing fs/config.json: copy config.example.json or the block from the SmartPot connection "
                         "guide (it is the same file the Rust firmware uses)")
    try:
        json.loads(config.read_text(encoding="utf-8"))
    except ValueError as error:
        raise SystemExit("fs/config.json is not valid JSON: {}".format(error)) from None
    files = {}
    for path in sorted(source.iterdir()):
        if path.is_file() and path.name not in SKIPPED and path.suffix in (".py", ".json"):
            files[path.name] = path.read_bytes()
    return files


def filesystem(size, context=None):
    return LittleFS(context=context, block_size=4096, block_count=size // 4096, read_size=LFS_READ_SIZE,
                    prog_size=LFS_PROG_SIZE, cache_size=LFS_CACHE_SIZE, lookahead_size=LFS_LOOKAHEAD,
                    block_cycles=100, mount=context is not None)


def littlefs_image(files, size):
    fs = filesystem(size)
    fs.format()
    fs.mount()
    for name, data in files.items():
        with fs.open(name, "wb") as handle:
            handle.write(data)
    fs.unmount()
    return bytes(fs.context.buffer)


def verify(image, files, size):
    """Mounts the image again with the MicroPython settings and compares every file."""
    fs = filesystem(size, UserContext(buffer=bytearray(image)))
    names = sorted(fs.listdir("/"))
    if names != sorted(files):
        raise SystemExit("LittleFS image lists {} instead of {}".format(names, sorted(files)))
    for name, data in files.items():
        with fs.open(name, "rb") as handle:
            if handle.read() != data:
                raise SystemExit("LittleFS image has a different " + name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--firmware", type=pathlib.Path, default=FIRMWARE)
    parser.add_argument("--source", type=pathlib.Path, default=SOURCE)
    parser.add_argument("--output", type=pathlib.Path, default=BUILD)
    args = parser.parse_args(argv)

    firmware = args.firmware.read_bytes()
    offset, size = find_partition(firmware, "vfs")
    if FIRMWARE_OFFSET + len(firmware) > offset:
        raise SystemExit("The MicroPython image overlaps the vfs partition")
    files = firmware_files(args.source)
    image = littlefs_image(files, size)
    verify(image, files, size)

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "littlefs.bin").write_bytes(image)
    # Rutas relativas a la carpeta de flasher_args.json, como las lee Wokwi.
    firmware_path = pathlib.Path(os.path.relpath(args.firmware.resolve(), args.output.resolve()))
    flasher_args = {
        "flash_settings": {"flash_mode": "dio", "flash_size": FLASH_SIZE, "flash_freq": "40m"},
        "flash_files": {
            "0x{:x}".format(FIRMWARE_OFFSET): firmware_path.as_posix(),
            "0x{:x}".format(offset): "littlefs.bin",
        },
        "extra_esptool_args": {"chip": "esp32"},
    }
    (args.output / "flasher_args.json").write_text(json.dumps(flasher_args, indent=2) + "\n", encoding="utf-8")
    print("Flash image ready: {} at 0x{:x} and LittleFS ({} files) at 0x{:x}".format(
        args.firmware.name, FIRMWARE_OFFSET, len(files), offset))
    print("Files: " + ", ".join(sorted(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
