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


# Rango de la API (`SmartPot-API`, validateDuration) y mensajes compartidos con el firmware Rust.
MIN_DURATION_SECONDS = 1
MAX_DURATION_SECONDS = 3600
MALFORMED = "Invalid command: id, actuator and action are required"
INVALID_DURATION = "durationSeconds must be an integer between 1 and 3600"
ACK_QUEUE_SIZE = 16
SOCKET_TIMEOUT_SECONDS = 15


def validate_command(payload):
    """Returns (command, command_id, reason): the command when valid, otherwise the id (if readable) and why.

    A negative, zero, decimal or out-of-range duration is rejected instead of being read as "no duration".
    """
    try:
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode()
        command = json.loads(payload)
    except (ValueError, TypeError):
        return None, None, MALFORMED
    if not isinstance(command, dict):
        return None, None, MALFORMED
    command_id = command.get("id")
    # El id debe ser texto no vacío: None, booleanos, números, listas y diccionarios no valen.
    if not isinstance(command_id, str) or not command_id.strip():
        return None, None, MALFORMED
    actuator = command.get("actuator")
    action = command.get("action")
    if not isinstance(actuator, str) or not isinstance(action, str) or not actuator.strip() or not action.strip():
        return None, command_id, MALFORMED
    duration = command.get("durationSeconds")
    # Solo se admite None o un entero entre 1 y 3600; bool es subclase de int y se excluye.
    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, int)
                                 or not MIN_DURATION_SECONDS <= duration <= MAX_DURATION_SECONDS):
        return None, command_id, INVALID_DURATION
    # `LIGHTS` es el alias heredado del contrato anterior; UV_LIGHT es el canónico.
    canonical_actuator = actuator.strip().upper()
    if canonical_actuator == "LIGHTS":
        canonical_actuator = "UV_LIGHT"
    return {
        "id": command_id,
        "actuator": canonical_actuator,
        "action": action.upper(),
        "durationSeconds": duration,
    }, command_id, None


def parse_command(payload):
    return validate_command(payload)[0]


def ack_payload(command_id, executed, message):
    return json.dumps({"id": command_id, "status": "EXECUTED" if executed else "FAILED", "message": message})


def ca_certificate(ca_data):
    """Decodes the broker CA in DER format from BROKER["CA_CRT"] PEM; without it the device key is never sent.

    config.json stores PEM text because Wokwi does not support .crt files; mbedtls reads DER in any
    MicroPython build even without PEM support.
    """
    import binascii

    begin, end = "-----BEGIN CERTIFICATE-----", "-----END CERTIFICATE-----"
    if not isinstance(ca_data, str) or begin not in ca_data or end not in ca_data:
        raise ValueError("config.json is missing the broker CA: copy the BROKER block from the connection guide")
    body = ca_data.split(begin, 1)[1].split(end, 1)[0]
    try:
        return binascii.a2b_base64("".join(body.split()))
    except ValueError:
        raise ValueError("the CA in config.json is corrupted: copy the BROKER block again")


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
    return "could not verify the broker ({}): check the CA in config.json and the system clock".format(detail)


class TimeoutSocket:
    """Socket wrapper that turns umqtt's blocking mode into a bounded timeout.

    umqtt.simple switches the socket to blocking mode to read the rest of a packet and to wait for PUBACK; without
    a timeout a half-open connection would block the device forever. Here "blocking" means "wait at most
    `timeout` seconds", so a dead link raises OSError and the device reconnects.
    """

    def __init__(self, sock, timeout):
        self._sock = sock
        self._timeout = timeout
        self._apply(timeout)

    def _apply(self, value):
        try:
            self._sock.settimeout(value)
        except (AttributeError, OSError):
            self._sock.setblocking(value != 0)

    def setblocking(self, flag):
        self._apply(self._timeout if flag else 0)

    def settimeout(self, value):
        self._apply(value)

    def __getattr__(self, name):
        return getattr(self._sock, name)


class SmartPotClient:
    def __init__(self, crop_id, device_key, host, port, use_tls, ca_data, on_command, client_factory=None,
                 capabilities=None):
        self.crop_id = crop_id
        self.host = host
        self.use_tls = use_tls
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
        self.pending_acks = []

    def connect(self):
        self.client.set_last_will(self.topics["status"], b"offline", retain=True, qos=1)
        try:
            self.client.connect()
        except OSError:
            raise
        except Exception as error:
            if type(error).__name__ == "MQTTException":
                # El broker respondió: el TLS se verificó y rechazó la clave del dispositivo.
                if self.use_tls:
                    print("SMARTPOT-TLS-OK host={}".format(self.host))
                print("SMARTPOT-MQTT-REFUSED")
            raise OSError(refusal(error))
        if self.use_tls:
            print("SMARTPOT-TLS-OK host={}".format(self.host))
        self.client.publish(self.topics["status"], b"online", retain=True, qos=1)
        if self.capabilities is not None:
            # Retenido para que la API conozca el hardware antes de enviar comandos.
            self.client.publish(self.topics["capabilities"], self.capabilities, retain=True, qos=1)
        self.client.subscribe(self.topics["commands"], qos=1)
        sock = getattr(self.client, "sock", None)
        if sock is not None and not isinstance(sock, TimeoutSocket):
            self.client.sock = TimeoutSocket(sock, SOCKET_TIMEOUT_SECONDS)
        print("MQTT connected as", self.crop_id)
        print("SMARTPOT-MQTT-UP crop={}".format(self.crop_id))

    def poll(self):
        self.client.check_msg()
        self.flush_acks()

    def ping(self):
        """Sends PINGREQ so the broker keeps the session alive even without telemetry."""
        self.client.ping()

    def publish_telemetry(self, readings):
        self.client.publish(self.topics["telemetry"], telemetry_payload(readings).encode())

    def acknowledge(self, command_id, executed, message):
        """Queues the ACK: it is published after check_msg returns, never inside the message callback.

        Publishing with QoS 1 inside the callback waited for PUBACK in the middle of check_msg and blocked the
        loop that turns timed actuators off.
        """
        print("SMARTPOT-ACK {} {} {}".format(command_id, "EXECUTED" if executed else "FAILED", message))
        self.pending_acks.append(ack_payload(command_id, executed, message).encode())
        if len(self.pending_acks) > ACK_QUEUE_SIZE:
            self.pending_acks.pop(0)

    def flush_acks(self):
        while self.pending_acks:
            self.client.publish(self.topics["ack"], self.pending_acks[0], qos=1)
            self.pending_acks.pop(0)

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
        command, command_id, reason = validate_command(message)
        if command is None:
            print("Unreadable command discarded:", reason)
            if command_id is not None:
                self.acknowledge(command_id, False, reason)
            return
        self.on_command(command)
