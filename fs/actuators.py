from machine import Pin

COMMAND_CACHE_SIZE = 16

# Política de seguridad compartida con el firmware Rust: la bomba y el
# humidificador nunca quedan encendidos sin límite ni sin supervisión.
MAX_DURATION_SECONDS = 3600
OFFLINE_CUTOFF_SECONDS = 300
CRITICAL_ACTUATORS = ("WATER_PUMP", "HUMIDIFIER")


def readable(seconds):
    """Formats a duration in human-readable form: 15 s, 10 min, 2 h."""
    if seconds % 3600 == 0:
        return "{} h".format(seconds // 3600)
    if seconds % 60 == 0:
        return "{} min".format(seconds // 60)
    return "{} s".format(seconds)


class Actuator:
    """Digital output (relay or LED in simulation) with optional automatic timed shutoff."""

    def __init__(self, name, pin_num, feminine=False, active_low=False):
        self.name = name
        # Con módulos de relé activos en bajo, la salida en alto los mantiene apagados.
        self.active_low = active_low
        # Se conserva el parámetro opcional por compatibilidad con llamadas previas.
        self.feminine = feminine
        # Clave del contrato (WATER_PUMP, ...); la asigna el banco de actuadores.
        self.key = name
        self.pin = Pin(pin_num, Pin.OUT)
        self.active = False
        self.until = None
        self.on_since = None
        self._drive(False)

    def _drive(self, on):
        self.pin.value(0 if on == self.active_low else 1)

    def turn_on(self, duration=None, now=0):
        self._drive(True)
        self.active = True
        self.on_since = now
        self.until = now + duration if duration else None
        if duration:
            print("SMARTPOT-ACT", self.key, "ON", "{}s".format(duration))
        else:
            print("SMARTPOT-ACT", self.key, "ON")

    def turn_off(self, reason="command", announce=True):
        was_active = self.active
        self._drive(False)
        self.active = False
        self.until = None
        self.on_since = None
        if was_active and announce:
            print("SMARTPOT-ACT", self.key, "OFF", reason)

    def tick(self, now):
        if self.active and self.until is not None and now >= self.until:
            self.turn_off("timer")
            print(self.name, "turned off after timer expired")


class ActuatorBank:
    """Executes API commands on connected actuators."""

    def __init__(self, actuators, offline_cutoff=OFFLINE_CUTOFF_SECONDS):
        self.actuators = actuators
        self.offline_cutoff = offline_cutoff
        self._recent = []
        for key, actuator in actuators.items():
            actuator.key = key

    def execute(self, command, now):
        command_id = command.get("id")
        if command_id is not None:
            for cached in self._recent:
                if cached[0] == command_id:
                    # Un reenvío QoS 1 no debe accionar el hardware dos veces: se repite el ACK anterior.
                    return cached[1], cached[2]
        result = self._apply(command, now)
        if command_id is not None:
            self._recent.append((command_id, result[0], result[1]))
            if len(self._recent) > COMMAND_CACHE_SIZE:
                self._recent.pop(0)
        return result

    def _apply(self, command, now):
        actuator = self.actuators.get(command["actuator"])
        if actuator is None:
            return False, "This device does not have " + command["actuator"]
        if command["action"] == "ACTIVATE":
            duration = command.get("durationSeconds")
            capped = duration is None and command["actuator"] in CRITICAL_ACTUATORS
            if capped:
                duration = MAX_DURATION_SECONDS
            actuator.turn_on(duration, now)
            state = " turned on"
            message = actuator.name + (state + " for " + readable(duration) if duration else state)
            return True, message + (" (safety limit)" if capped else "")
        if command["action"] == "DEACTIVATE":
            actuator.turn_off()
            return True, actuator.name + " turned off"
        return False, "Unknown action: " + command["action"]

    def tick(self, now, offline_since=None):
        """Applies timers and, without a broker, the failsafe of critical actuators."""
        for key, actuator in self.actuators.items():
            actuator.tick(now)
            if offline_since is None or not actuator.active or key not in CRITICAL_ACTUATORS:
                continue
            # Se cuenta desde lo más reciente: el corte o el encendido.
            since = max(offline_since, actuator.on_since or 0)
            if now - since >= self.offline_cutoff:
                actuator.turn_off("failsafe")

    def all_off(self, reason="safety", announce=True):
        """Turns every output off. Emergency stops pass announce=False: the SMARTPOT-FATAL line says it all,
        as in the Rust firmware, whose panic handler clears the GPIO register without printing."""
        for actuator in self.actuators.values():
            actuator.turn_off(reason, announce)

    def states(self):
        return {name: actuator.active for name, actuator in self.actuators.items()}
