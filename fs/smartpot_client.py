"""Device MQTT client implementing the SmartPot v1 contract.

Formatting functions are hardware-independent and tested under CPython.
"""

import json

TOPIC_PREFIX = "smartpot/v1"
TELEMETRY_FIELDS = ("temperature", "humidity", "brightness", "ph", "tds", "soilMoisture", "atmosphere")
DECIMALS = {"brightness": 0, "tds": 0, "ph": 2}


def topics(crop_id, prefix=TOPIC_PREFIX):
    base = prefix + "/" + crop_id
    return {
        "telemetry": base + "/telemetry",
        "commands": base + "/commands",
        "ack": base + "/commands/ack",
        "status": base + "/status",
        "capabilities": base + "/capabilities",
    }


def telemetry_payload(readings):
    data = {}
    for field in TELEMETRY_FIELDS:
        value = readings.get(field)
        if value is None:
            continue
        decimals = DECIMALS.get(field, 1)
        # Con 0 decimales el campo debe ser un entero JSON: 813, no 813.0.
        data[field] = int(round(value)) if decimals == 0 else round(value, decimals)
    return json.dumps(data)


def capabilities_payload():
    """Declara los sensores y actuadores que este firmware MicroPython tiene instalados."""
    return json.dumps({
        "schema": 1,
        "device": {
            "firmware": "smartpot-iot-python/2.0.0",
            "hardware": "esp32-devkit-c-v4",
            "telemetry": {"formats": ["json-v1"]},
        },
        "sensors": [
            {"key": "temperature", "unit": "C", "resolution": 0.01, "range": [-20, 60]},
            {"key": "humidity", "unit": "%", "resolution": 0.01, "range": [0, 100]},
            {"key": "brightness", "unit": "lux", "resolution": 1, "range": [0, 2000]},
            {"key": "ph", "unit": "pH", "resolution": 0.01, "range": [0, 14]},
            {"key": "tds", "unit": "ppm", "resolution": 1, "range": [0, 3000]},
            {"key": "soilMoisture", "unit": "%", "resolution": 0.01, "range": [0, 100]},
        ],
        "actuators": ["WATER_PUMP", "UV_LIGHT", "FAN"],
    })


def parse_command(payload):
    try:
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode()
        command = json.loads(payload)
        if not isinstance(command, dict):
            return None
        command_id = command.get("id")
        # El id debe ser texto no vacío: None, booleanos, números, listas y diccionarios no valen.
        if not isinstance(command_id, str) or not command_id.strip():
            return None
        actuator = command.get("actuator")
        action = command.get("action")
        if not isinstance(actuator, str) or not isinstance(action, str):
            return None
        if not actuator.strip() or not action.strip():
            return None
        duration = command.get("durationSeconds")
        # Solo se admite None o un entero no negativo; bool es subclase de int y se excluye.
        if duration is not None and (isinstance(duration, bool) or not isinstance(duration, int) or duration < 0):
            return None
        return {
            "id": command_id,
            "actuator": actuator.upper(),
            "action": action.upper(),
            "durationSeconds": duration,
        }
    except (ValueError, KeyError, TypeError):
        return None


def ack_payload(command_id, executed, message):
    return json.dumps({"id": command_id, "status": "EXECUTED" if executed else "FAILED", "message": message})


def ca_certificate(ca_data):
    """Decodes the broker CA in DER format from BROKER["CA_CRT"] PEM; without it the device key is never sent.

    config.py stores PEM text because Wokwi does not support .crt files; mbedtls reads DER in any
    MicroPython build even without PEM support.
    """
    import binascii

    begin, end = "-----BEGIN CERTIFICATE-----", "-----END CERTIFICATE-----"
    if not isinstance(ca_data, str) or begin not in ca_data or end not in ca_data:
        raise ValueError("config.py is missing the broker CA: copy the BROKER block from the connection guide")
    body = ca_data.split(begin, 1)[1].split(end, 1)[0]
    try:
        return binascii.a2b_base64("".join(body.split()))
    except ValueError:
        raise ValueError("the CA in config.py is corrupted: copy the BROKER block again")


def tls_client(factory, client_id, host, options, ca_data):
    """Builds a TLS MQTT client that verifies the broker certificate against the CA and hostname.

    umqtt.simple in MicroPython 1.22 takes ssl=True and ssl_params; if given an SSLContext it ignores it and encrypts
    without verification. Since 1.23 it accepts an SSLContext and rejects ssl_params, so 1.22 form is tried first.
    """
    import ssl

    ca = ca_certificate(ca_data)
    try:
        return factory(client_id, host, ssl=True, ssl_params={
            "server_hostname": host, "cert_reqs": ssl.CERT_REQUIRED, "cadata": ca}, **options)
    except TypeError:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_verify_locations(cadata=ca)
        return factory(client_id, host, ssl=context, **options)


def refusal(error):
    """Formats why the connection was refused when not a network error: device key or broker verification."""
    detail = str(error).strip()
    # umqtt.simple lanza MQTTException con el código del CONNACK; mbedtls, un error con el motivo de la verificación.
    if type(error).__name__ == "MQTTException":
        return "broker rejected the device key (code {})".format(detail)
    return "could not verify the broker ({}): check the CA in config.py and the system clock".format(detail)


class SmartPotClient:
    def __init__(self, crop_id, device_key, host, port, use_tls, ca_data, on_command, client_factory=None,
                 capabilities=None):
        self.crop_id = crop_id
        self.topics = topics(crop_id)
        self.on_command = on_command
        # Se codifica una sola vez; None mantiene la publicación de capacidades desactivada.
        self.capabilities = capabilities.encode() if isinstance(capabilities, str) else capabilities
        if client_factory is None:
            from umqtt.simple import MQTTClient

            client_factory = MQTTClient
        options = {"port": port, "user": crop_id, "password": device_key, "keepalive": 60}
        client_id = "smartpot-" + crop_id
        if use_tls:
            self.client = tls_client(client_factory, client_id, host, options, ca_data)
        else:
            self.client = client_factory(client_id, host, **options)
        self.client.set_callback(self._on_message)

    def connect(self):
        self.client.set_last_will(self.topics["status"], b"offline", retain=True, qos=1)
        try:
            self.client.connect()
        except OSError:
            raise
        except Exception as error:
            raise OSError(refusal(error))
        self.client.publish(self.topics["status"], b"online", retain=True, qos=1)
        if self.capabilities is not None:
            # Retenido para que la API conozca el hardware antes de enviar comandos.
            self.client.publish(self.topics["capabilities"], self.capabilities, retain=True, qos=1)
        self.client.subscribe(self.topics["commands"], qos=1)
        print("MQTT connected as", self.crop_id)

    def poll(self):
        self.client.check_msg()

    def publish_telemetry(self, readings):
        self.client.publish(self.topics["telemetry"], telemetry_payload(readings).encode())

    def acknowledge(self, command_id, executed, message):
        self.client.publish(self.topics["ack"], ack_payload(command_id, executed, message).encode(), qos=1)

    def disconnect(self):
        try:
            self.client.publish(self.topics["status"], b"offline", retain=True, qos=1)
            self.client.disconnect()
        except OSError:
            pass

    def close(self):
        """Releases the socket of a dropped connection before reconnecting."""
        sock = getattr(self.client, "sock", None)
        if sock is None:
            return
        try:
            sock.close()
        except OSError as error:
            print("Socket was already closed:", error)

    def _on_message(self, topic, message):
        command = parse_command(message)
        if command is None:
            print("Unreadable command discarded")
            return
        self.on_command(command)
