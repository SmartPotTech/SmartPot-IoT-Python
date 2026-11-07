from i2c_lcd import I2cLcd
from machine import I2C, Pin

# Glifos de la ROM A00 del HD44780 («｡», «･», «ロ»): un punto que crece. La ROM no tiene «\», que se ve como «¥».
SPINNER = "\xa1\xa5\xdb"
# Íconos propios en la CGRAM: chr(0) es la antena (en línea) y chr(1) la equis (sin conexión con el broker).
ONLINE, OFFLINE = chr(0), chr(1)
ICONS = (
    (0b00000, 0b01110, 0b10001, 0b00100, 0b01010, 0b00000, 0b00100, 0b00000),
    (0b00000, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001, 0b00000, 0b00000),
)
# Cada cuántos refrescos se vuelve a buscar una pantalla que no respondió.
REPROBE_EVERY = 10


def _number(readings, name, width, decimals=0):
    number = readings.get(name)
    if number is None:
        return " " * (width - 2) + "--"
    # MicroPython no admite campos anidados en la especificación de formato.
    return ("{:" + str(width) + "." + str(decimals) + "f}").format(number)


class LCDDisplay:
    """20x4 I2C LCD display. The HD44780 controller uses ASCII characters without accents."""

    def __init__(self, scl_pin, sda_pin, address=0x27, rows=4, cols=20, freq=400000):
        self.i2c = I2C(scl=Pin(scl_pin), sda=Pin(sda_pin), freq=freq)
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
    again every few refreshes, so it comes back by itself when reconnected.
    """

    def __init__(self, factory):
        self.factory = factory
        self.display = NullDisplay()
        self.skipped = 0
        self._open()

    def _open(self):
        try:
            self.display = self.factory()
            return True
        except Exception as error:
            print("LCD not available:", error)
            self.display = NullDisplay()
            return False

    def _call(self, name, *args):
        if isinstance(self.display, NullDisplay):
            self.skipped += 1
            if self.skipped % REPROBE_EVERY or not self._open():
                return
        try:
            getattr(self.display, name)(*args)
        except Exception as error:
            print("LCD error:", error)
            self.display = NullDisplay()

    def show_message(self, first, second=""):
        self._call("show_message", first, second)

    def show_spinner(self):
        self._call("show_spinner")

    def show_readings(self, readings, states, online=False):
        self._call("show_readings", readings, states, online)
