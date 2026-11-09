import errno

import dht
from machine import ADC, Pin

# Escala exacta del ADC de 12 bits del ESP32, igual que `linear_scale` del firmware Rust. `read_u16` no sirve:
# MicroPython lo aproxima con `raw << 4 | raw >> 8` y el segundo decimal de la tabla dejaba de coincidir.
ADC_MAX = 4095
# Lecturas que se promedian por canal: el ADC del ESP32 es ruidoso y 16 muestras tardan menos de 1 ms.
OVERSAMPLING = 16
# Los sensores se muestrean cada 2 s (el mínimo del DHT22) y una lectura caduca tras tres periodos.
SAMPLE_SECONDS = 2
FRESHNESS_SECONDS = 3 * SAMPLE_SECONDS
FIELDS = ("temperature", "humidity", "brightness", "ph", "tds", "soilMoisture")


class SensorError(Exception):
    """A sensor did not answer; the message is the reason shown on the console."""


class ADCSensor:
    """ESP32 analog sensor (potentiometer in simulation) with linear scale."""

    scale = (0.0, 100.0)

    def __init__(self, pin_num):
        self.adc = ADC(Pin(pin_num))
        self.adc.atten(ADC.ATTN_11DB)

    def read(self):
        total = count = 0
        for _ in range(OVERSAMPLING):
            try:
                total += self.adc.read()
                count += 1
            except OSError:
                pass
        if not count:
            return None
        low, high = self.scale
        return low + min(total // count, ADC_MAX) * (high - low) / ADC_MAX


class LightSensor(ADCSensor):
    scale = (0.0, 2000.0)


class PHSensor(ADCSensor):
    scale = (0.0, 14.0)


class TDSSensor(ADCSensor):
    scale = (0.0, 3000.0)


class SoilMoistureSensor(ADCSensor):
    scale = (0.0, 100.0)


class AtmosphereSensor:
    """DHT22: air temperature and relative humidity."""

    def __init__(self, pin_num):
        self.sensor = dht.DHT22(Pin(pin_num))

    def read(self):
        """Returns (temperature, humidity) or raises SensorError with the same reasons as the Rust driver."""
        try:
            self.sensor.measure()
        except OSError as error:
            # dht_readinto agota el tiempo tanto si el sensor no contesta como si corta la trama.
            if error.args and error.args[0] == errno.ETIMEDOUT:
                raise SensorError("no response")
            raise SensorError(str(error))
        except Exception:
            # El módulo dht de MicroPython lanza Exception("checksum error") si la trama llega dañada.
            raise SensorError("checksum mismatch")
        return self.sensor.temperature(), self.sensor.humidity()


class SensorHub:
    """Samples every sensor every 2 s and keeps the last good value of each field.

    A reading taken a few seconds ago is still valid when the snapshot is printed, so a single failed DHT22
    read does not blank the table; after three sampling periods without an answer the field shows "--".
    """

    def __init__(self, atmosphere, analog, sample_seconds=SAMPLE_SECONDS):
        self.atmosphere = atmosphere
        self.analog = analog
        self.sample_seconds = sample_seconds
        self.freshness = 3 * sample_seconds
        self.values = {}
        self.next_sample = 0
        self.atmosphere_error = None

    def sample(self, now):
        if now < self.next_sample:
            return
        self.next_sample = now + self.sample_seconds
        try:
            temperature, humidity = self.atmosphere.read()
            self.values["temperature"] = (temperature, now)
            self.values["humidity"] = (humidity, now)
            self.atmosphere_error = None
        except SensorError as error:
            self.atmosphere_error = str(error)
        for name, sensor in self.analog.items():
            value = sensor.read()
            if value is not None:
                self.values[name] = (value, now)

    def snapshot(self, now):
        readings = {}
        for field in FIELDS:
            entry = self.values.get(field)
            readings[field] = entry[0] if entry and now - entry[1] <= self.freshness else None
        if readings["temperature"] is None and readings["humidity"] is None:
            print("Error reading DHT22:", self.atmosphere_error or "no response")
        return readings
