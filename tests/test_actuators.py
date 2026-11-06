from actuators import COMMAND_CACHE_SIZE, ActuatorBank


class CountingActuator:
    """Registra cuántas veces se accionó el hardware, sin tocar pines reales."""

    def __init__(self, name="Fan"):
        self.name = name
        self.active = False
        self.until = None
        self.on_calls = 0
        self.off_calls = 0

    def turn_on(self, duration=None, now=0):
        self.on_calls += 1
        self.active = True
        self.until = now + duration if duration else None

    def turn_off(self):
        self.off_calls += 1
        self.active = False
        self.until = None

    def tick(self, now):
        pass


def activate(command_id=None, duration=15):
    command = {"actuator": "FAN", "action": "ACTIVATE", "durationSeconds": duration}
    if command_id is not None:
        command["id"] = command_id
    return command


def test_a_repeated_id_runs_the_command_only_once_and_repeats_the_ack():
    fan = CountingActuator()
    bank = ActuatorBank({"FAN": fan})
    first = bank.execute(activate("c1"), 100)
    second = bank.execute(activate("c1"), 200)
    assert first == second == (True, "Fan turned on for 15 s")
    assert fan.on_calls == 1
    assert fan.until == 115


def test_a_duplicate_id_does_not_reverse_the_previous_action():
    fan = CountingActuator()
    bank = ActuatorBank({"FAN": fan})
    bank.execute(activate("c2"), 0)
    repeated = bank.execute({"id": "c2", "actuator": "FAN", "action": "DEACTIVATE"}, 1)
    assert repeated == (True, "Fan turned on for 15 s")
    assert fan.active is True
    assert fan.off_calls == 0


def test_failed_results_are_cached_too():
    bank = ActuatorBank({"FAN": CountingActuator()})
    command = {"id": "c3", "actuator": "HUMIDIFIER", "action": "ACTIVATE"}
    first = bank.execute(command, 0)
    second = bank.execute(command, 1)
    assert first == second == (False, "This device does not have HUMIDIFIER")


def test_commands_without_an_id_are_always_executed():
    fan = CountingActuator()
    bank = ActuatorBank({"FAN": fan})
    assert bank.execute(activate(), 0) == (True, "Fan turned on for 15 s")
    assert bank.execute(activate(), 1) == (True, "Fan turned on for 15 s")
    assert fan.on_calls == 2


def test_the_cache_keeps_only_the_last_16_ids():
    fan = CountingActuator()
    bank = ActuatorBank({"FAN": fan})
    for number in range(COMMAND_CACHE_SIZE):
        bank.execute({"id": "c{}".format(number), "actuator": "FAN", "action": "DEACTIVATE"}, 0)
    assert fan.off_calls == COMMAND_CACHE_SIZE
    bank.execute({"id": "c16", "actuator": "FAN", "action": "DEACTIVATE"}, 0)
    assert fan.off_calls == COMMAND_CACHE_SIZE + 1
    # c0 ya salió de la ventana: se vuelve a ejecutar.
    bank.execute({"id": "c0", "actuator": "FAN", "action": "DEACTIVATE"}, 0)
    assert fan.off_calls == COMMAND_CACHE_SIZE + 2
    # c16 sigue dentro de la ventana: no se repite.
    bank.execute({"id": "c16", "actuator": "FAN", "action": "DEACTIVATE"}, 0)
    assert fan.off_calls == COMMAND_CACHE_SIZE + 2
