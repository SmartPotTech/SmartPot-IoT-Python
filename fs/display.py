from i2c_lcd import I2cLcd
from machine import Pin, SoftI2C

# Glifos de la ROM A00 del HD44780 («｡», «･», «ロ»): un punto que crece. La ROM no tiene «\», que se ve como «¥».
SPINNER = "\xa1\xa5\xdb"
# Íconos propios en la CGRAM: chr(0) es la antena (en línea) y chr(1) la equis (sin conexión con el broker).
ONLINE, OFFLINE = chr(0), chr(1)
ICONS = (
    (0b00000, 0b01110, 0b10001, 0b00100, 0b01010, 0b00000, 0b00100, 0b00000),
    (0b00000, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001, 0b00000, 0b00000),
)
# Cada cuántos segundos se vuelve a buscar una pantalla que no respondió.
REPROBE_SECONDS = 10
LCD_ADDRESS = 0x27
# Se imprime una vez cada vez que la pantalla deja de responder (texto compartido con el firmware Rust).
UNAVAILABLE = "LCD not available at 0x{:02X}".format(LCD_ADDRESS)


def _number(readings, name, width, decimals=0):
    number = readings.get(name)
    if number is None:
        return " " * (width - 2) + "--"
    # MicroPython no admite campos anidados en la especificación de formato.
    return ("{:" + str(width) + "." + str(decimals) + "f}").format(number)


class LCDDisplay:
    """20x4 I2C LCD display. The HD44780 controller uses ASCII characters without accents."""

    def __init__(self, scl_pin, sda_pin, address=LCD_ADDRESS, rows=4, cols=20, freq=400000):
        # SoftI2C es el mismo bus por software que antes daba I2C(-1) sin el aviso de obsoleto.
        self.i2c = SoftI2C(scl=Pin(scl_pin), sda=Pin(sda_pin), freq=freq)
        self.lcd = I2cLcd(self.i2c, address, rows, cols)
        self.cols = cols
        for location, icon in enumerate(ICONS):
            self.lcd.custom_char(location, bytearray(icon))
        self.position = 0
        # Filas ya escritas: solo se reescriben las que cambian, sin borrar la pantalla.
        self.shown = [None] * rows
        self.renders = 0

    def print(self, text, row, col=0):
        self.lcd.move_to(col, row)
        self.lcd.putstr(text)

    def show_message(self, first, second=""):
        self.lcd.clear()
        self.shown = [None] * len(self.shown)
        self.print(first, 1, max(0, (self.cols - len(first)) // 2))
        if second:
            self.print(second, 2, max(0, (self.cols - len(second)) // 2))

    def show_spinner(self):
        self.print(SPINNER[self.position], 3, self.cols - 1)
        self.position = (self.position + 1) % len(SPINNER)

    def _fit(self, text):
        # Un texto más largo que la fila saltaría a otra línea del HD44780 (la 0 continúa en la 2).
        return (text + " " * self.cols)[:self.cols]

    def show_readings(self, readings, states, online=False):
        pump = "P" if states.get("WATER_PUMP") else "-"
        light = "L" if states.get("UV_LIGHT") else "-"
        fan = "F" if states.get("FAN") else "-"
        header = "T {}C H {}%".format(_number(readings, "temperature", 4, 1), _number(readings, "humidity", 3))
        rows = (
            self._fit(header)[:self.cols - 1] + (ONLINE if online else OFFLINE),
            self._fit("Light {} lux".format(_number(readings, "brightness", 5))),
            self._fit("Soil {}% pH {}".format(_number(readings, "soilMoisture", 3), _number(readings, "ph", 4, 1))),
            self._fit("TDS {}ppm [{}{}{}]".format(_number(readings, "tds", 4), pump, light, fan)),
        )
        for index, text in enumerate(rows):
            if self.shown[index] != text:
                self.print(text, index)
                self.shown[index] = text
        self.renders += 1
        # Marcador para las capturas automáticas de Wokwi: el render ya terminó.
        print("SMARTPOT-LCD n={}".format(self.renders))


class NullDisplay:
    """Stand-in while no LCD answers: the device keeps measuring and publishing."""

    def show_message(self, first, second=""):
        pass

    def show_spinner(self):
        pass

    def show_readings(self, readings, states, online=False):
        pass


class ResilientDisplay:
    """Wraps the LCD so that a missing or failing display never stops the firmware.

    If the LCD is absent at boot or fails later (I2C error), drawing switches to a no-op and the LCD is probed
    again every few refreshes, so it comes back by itself when reconnected. The console reports each loss once.
    """

    def __init__(self, factory):
        self.factory = factory
        self.display = NullDisplay()
        self.reported = False
        self.next_probe = 0
        # Queda en True cuando la pantalla vuelve: el dispositivo la redibuja entera.
        self.recovered = False
        self._open()

    def tick(self, now):
        """Probes a missing LCD every 10 s, like the display task of the Rust firmware."""
        if not isinstance(self.display, NullDisplay) or now < self.next_probe:
            return
        self.next_probe = now + REPROBE_SECONDS
        if self._open():
            self.recovered = True

    def _lost(self):
        self.display = NullDisplay()
        if not self.reported:
            print(UNAVAILABLE)
            self.reported = True

    def _open(self):
        try:
            self.display = self.factory()
        except Exception:
            self._lost()
            return False
        self.reported = False
        return True

    def _call(self, name, *args):
        if isinstance(self.display, NullDisplay):
            return
        try:
            getattr(self.display, name)(*args)
        except Exception:
            self._lost()

    def show_message(self, first, second=""):
        self._call("show_message", first, second)

    def show_spinner(self):
        self._call("show_spinner")

    def show_readings(self, readings, states, online=False):
        self._call("show_readings", readings, states, online)
