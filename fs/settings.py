"""Loads the device configuration shared with the Rust firmware.

`config.json` has the same blocks and keys in both firmwares (WIFI, SMARTPOT and BROKER with the CA as PEM text);
this firmware reads it at runtime and the Rust one at build time. The SmartPot connection guide generates it.
"""

import json

CONFIG_FILE = "config.json"


def load(path=CONFIG_FILE):
    try:
        with open(path) as handle:
            data = json.load(handle)
    except OSError:
        # Sin config.json no hay red ni cultivo: se avisa en vez de arrancar a medias.
        raise OSError(
            "Missing " + path + ": copy config.example.json or the block from the SmartPot connection guide"
        ) from None
    data.setdefault("BROKER", {})
    return data
