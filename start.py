"""Runs the firmware in Wokwi simulation (VS Code or wokwi-cli) by mounting the fs directory."""

import subprocess
import sys

COMMAND = [sys.executable, "-m", "mpremote", "connect", "port:rfc2217://localhost:4000",
           "mount", "fs", "exec", "import main"]

if __name__ == "__main__":
    try:
        subprocess.run(COMMAND, check=True)
    except subprocess.CalledProcessError as error:
        sys.exit(f"Firmware exited with error: {error}")
    except FileNotFoundError:
        sys.exit("mpremote not found: run 'uv sync' before starting the simulation.")
