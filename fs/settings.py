"""Loads the device configuration shared with the Rust firmware.

`config.json` has the same blocks and keys in both firmwares (WIFI, SMARTPOT and BROKER with the CA as PEM text);
this firmware reads it at runtime and the Rust one at build time. Boards configured with the older `config.py`
keep working: it is read when no `config.json` exists.
"""

import json

CONFIG_FILE = "config.json"


def load(path=CONFIG_FILE):
    try:
        with open(path) as handle:
            data = json.load(handle)
    except OSError:
        # Formato anterior: módulo config.py con los mismos bloques.
        import config

        data = {"WIFI": config.WIFI, "SMARTPOT": config.SMARTPOT, "BROKER": getattr(config, "BROKER", {})}
    data.setdefault("BROKER", {})
    return data
