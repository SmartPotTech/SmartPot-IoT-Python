from machine import Pin

COMMAND_CACHE_SIZE = 16


def readable(seconds):
    """Formats a duration in human-readable form: 15 s, 10 min, 2 h."""
    if seconds % 3600 == 0:
        return "{} h".format(seconds // 3600)
    if seconds % 60 == 0:
        return "{} min".format(seconds // 60)
    return "{} s".format(seconds)


class Actuator:
    """Digital output (relay or LED in simulation) with optional automatic timed shutoff."""

    def __init__(self, name, pin_num, feminine=False):
        self.name = name
        # Se conserva el parámetro opcional por compatibilidad con llamadas previas.
        self.feminine = feminine
        self.pin = Pin(pin_num, Pin.OUT)
        self.active = False
        self.until = None
        self.pin.off()

    def turn_on(self, duration=None, now=0):
        self.pin.on()
        self.active = True
        self.until = now + duration if duration else None

    def turn_off(self):
        self.pin.off()
        self.active = False
        self.until = None

    def tick(self, now):
        if self.active and self.until is not None and now >= self.until:
            self.turn_off()
            print(self.name, "turned off after timer expired")


class ActuatorBank:
    """Executes API commands on connected actuators."""

    def __init__(self, actuators):
        self.actuators = actuators
        self._recent = []

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
            actuator.turn_on(duration, now)
            state = " turned on"
            return True, actuator.name + (state + " for " + readable(duration) if duration else state)
        if command["action"] == "DEACTIVATE":
            actuator.turn_off()
            return True, actuator.name + " turned off"
        return False, "Unknown action: " + command["action"]

    def tick(self, now):
        for actuator in self.actuators.values():
            actuator.tick(now)

    def states(self):
        return {name: actuator.active for name, actuator in self.actuators.items()}
