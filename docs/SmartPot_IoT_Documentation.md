<!-- portada
eyebrow: Component Documentation · Documentación del componente
titulo: SmartPot-IoT
acento: IoT
subtitulo: Real Crop Firmware · El firmware de un cultivo real
bajada: MicroPython on ESP32 (physical or Wokwi): sensors, LCD, actuators, and MQTT v1 over TLS · MicroPython en un ESP32, físico o simulado en Wokwi: sensores, pantalla, actuadores y contrato MQTT v1 con TLS.
documento: SmartPot-IoT
version: 1.1 · October 2026
equipo: SmartPotTech
proyecto: smartpot.app
-->

# SmartPot-IoT

> **Language / Idioma:** [English](#english) | [Español](#español)

---

<a id="english"></a>

## English

### Document Sheet

| Field | Value |
| --- | --- |
| Project | SmartPot · [smartpot.app](https://smartpot.app) |
| Component | [SmartPot-IoT](https://github.com/SmartPotTech/SmartPot-IoT) |
| Version | 1.1 · October 2026 |
| Scope | Circuit, firmware modules, main loop, commands, secure connection, configuration, and tests |
| Platform Documentation | [Technical documentation](https://github.com/SmartPotTech/.github/blob/main/docs/SmartPot_Technical_Documentation.md), [project journey](https://github.com/SmartPotTech/.github/blob/main/docs/SmartPot_Project_Journey.md), [software lifecycle](https://github.com/SmartPotTech/.github/blob/main/docs/SmartPot_Software_Lifecycle.md), and [global diagrams](https://github.com/SmartPotTech/.github/blob/main/docs/README.md#diagramas-generales) |
| Maintenance | Generated from `docs/` in this repository using `.github/docs/tools`; updated with each component change |

<!-- parte: PART I | The Component -->

### 1. Purpose

#### In Plain Terms

SmartPot-IoT is the program running on the device of a **real crop**: an ESP32 with its sensors, actuators, and LCD display. It measures, displays, publishes readings to the SmartPot broker, and executes incoming commands while acknowledging each one. It makes no autonomous agronomic decisions—the API and AI assistant decide. The same firmware runs on a physical board or in [Wokwi](https://wokwi.com/projects/408863167711709185), and in both cases the crop is real to the platform; virtual crops, by contrast, are simulated by SmartPot-DataGenerator.

### 2. Component Architecture

<!-- diagrama: SmartPot_IoT_Global_Component | titulo=SmartPot-IoT por dentro | lamina=H -->
```mermaid
%%{init: {"theme": "base", "fontFamily": "Segoe UI, Arial, sans-serif", "themeVariables": {"fontFamily": "Segoe UI, Arial, sans-serif", "fontSize": "15px", "primaryColor": "#DDF5EA", "primaryTextColor": "#17261F", "primaryBorderColor": "#067A52", "secondaryColor": "#E3F2FB", "secondaryTextColor": "#17261F", "secondaryBorderColor": "#1F6FA0", "tertiaryColor": "#F2F7F4", "tertiaryTextColor": "#17261F", "tertiaryBorderColor": "#D5E3DC", "lineColor": "#5B6B63", "textColor": "#17261F", "mainBkg": "#DDF5EA", "nodeBorder": "#067A52", "clusterBkg": "#F7FAF8", "clusterBorder": "#D5E3DC", "edgeLabelBackground": "#FFFFFF", "actorBkg": "#067A52", "actorBorder": "#0B3D2B", "actorTextColor": "#FFFFFF", "actorLineColor": "#5B6B63", "signalColor": "#17261F", "signalTextColor": "#17261F", "labelBoxBkgColor": "#0B3D2B", "labelBoxBorderColor": "#0B3D2B", "labelTextColor": "#FFFFFF", "loopTextColor": "#0B3D2B", "noteBkgColor": "#FDF4DD", "noteBorderColor": "#C98D12", "noteTextColor": "#17261F", "activationBkgColor": "#DDF5EA", "activationBorderColor": "#067A52", "attributeBackgroundColorOdd": "#FFFFFF", "attributeBackgroundColorEven": "#F2F7F4"}, "layout": "elk", "elk": {"nodePlacementStrategy": "BRANDES_KOEPF", "mergeEdges": false, "cycleBreakingStrategy": "GREEDY"}}}%%
flowchart LR
  subgraph esp["ESP32 · MicroPython 1.23 · physical or Wokwi"]
    direction TB
    main["main.py<br/>WiFi · NTP · main loop"]
    config["config.py<br/>WIFI · SMARTPOT · BROKER with CA<br/>(untracked)"]
    client["smartpot_client.py<br/>SmartPotClient · v1 topics<br/>telemetry · commands · ACK · status"]
    sensors["sensors.py<br/>AtmosphereSensor DHT22<br/>Light · PH · TDS · SoilMoisture (ADC)"]
    actuators["actuators.py<br/>ActuatorBank · timed shutoff"]
    display["display.py<br/>LCDDisplay 20 × 4 over I2C"]
    utils["utils.py<br/>NTP clock · console table"]
  end
  subgraph hw["Circuit"]
    direction TB
    dht["DHT22 · GPIO 15"]
    adc["Light 34 · pH 35 · TDS 32 · substrate 33"]
    outs["Pump 19 · UV light 18 · fan 5"]
    lcd["LCD · SCL 16 · SDA 17"]
  end
  broker["mqtt.smartpot.app:8883<br/>TLS 1.2+"]
  main --> config & client & sensors & actuators & display & utils
  sensors --> dht & adc
  actuators --> outs
  display --> lcd
  client <-->|"user cropId · device key"| broker
  classDef leaf fill:#DDF5EA,stroke:#067A52,color:#17261F
  classDef water fill:#E3F2FB,stroke:#1F6FA0,color:#17261F
  classDef sun fill:#FDF4DD,stroke:#C98D12,color:#17261F
  classDef clay fill:#FBE9E1,stroke:#B85A38,color:#17261F
  classDef core fill:#067A52,stroke:#0B3D2B,color:#FFFFFF
  classDef deep fill:#0B3D2B,stroke:#06281C,color:#FFFFFF
  classDef muted fill:#F2F7F4,stroke:#5B6B63,color:#17261F
  class main core
  class config,utils muted
  class client,sensors,actuators,display leaf
  class dht,adc,outs,lcd clay
  class broker water
```

| Component | ESP32 Pin | Reported Scale |
| --- | --- | --- |
| DHT22 (air temperature and humidity) | GPIO 15 | °C and % |
| Light sensor (potentiometer in Wokwi) | GPIO 34 | 0–2000 lux |
| pH sensor | GPIO 35 | 0–14 |
| TDS sensor | GPIO 32 | 0–3000 ppm |
| Substrate moisture | GPIO 33 | 0–100 % |
| Water pump | GPIO 19 | `WATER_PUMP` |
| UV light | GPIO 18 | `UV_LIGHT` |
| Fan | GPIO 5 | `FAN` |
| 20×4 I2C LCD | SCL 16 · SDA 17 | — |

<!-- parte: PART II | Operation -->

### 3. Main Loop

<!-- diagrama: SmartPot_IoT_01_Main_Loop | titulo=Ciclo principal del firmware -->
```mermaid
%%{init: {"theme": "base", "fontFamily": "Segoe UI, Arial, sans-serif", "themeVariables": {"fontFamily": "Segoe UI, Arial, sans-serif", "fontSize": "15px", "primaryColor": "#DDF5EA", "primaryTextColor": "#17261F", "primaryBorderColor": "#067A52", "secondaryColor": "#E3F2FB", "secondaryTextColor": "#17261F", "secondaryBorderColor": "#1F6FA0", "tertiaryColor": "#F2F7F4", "tertiaryTextColor": "#17261F", "tertiaryBorderColor": "#D5E3DC", "lineColor": "#5B6B63", "textColor": "#17261F", "mainBkg": "#DDF5EA", "nodeBorder": "#067A52", "clusterBkg": "#F7FAF8", "clusterBorder": "#D5E3DC", "edgeLabelBackground": "#FFFFFF", "actorBkg": "#067A52", "actorBorder": "#0B3D2B", "actorTextColor": "#FFFFFF", "actorLineColor": "#5B6B63", "signalColor": "#17261F", "signalTextColor": "#17261F", "labelBoxBkgColor": "#0B3D2B", "labelBoxBorderColor": "#0B3D2B", "labelTextColor": "#FFFFFF", "loopTextColor": "#0B3D2B", "noteBkgColor": "#FDF4DD", "noteBorderColor": "#C98D12", "noteTextColor": "#17261F", "activationBkgColor": "#DDF5EA", "activationBorderColor": "#067A52", "attributeBackgroundColorOdd": "#FFFFFF", "attributeBackgroundColorEven": "#F2F7F4"}}}%%
flowchart TB
  boot(["Boot"]) --> wifi{"WiFi connected?<br/>up to 30 s"}
  wifi -->|"No"| wait["Wait 10 s"] --> wifi
  wifi -->|"Yes"| ntp["NTP clock sync"]
  ntp --> mqtt{"Connected to broker?"}
  mqtt -->|"No"| connect["CONNECT with TLS and config.py CA<br/>last will offline<br/>status online · subscribe to commands"]
  connect --> mqtt
  mqtt -->|"Yes"| poll["poll: handle incoming commands"]
  poll --> tick["bank.tick: turn off actuators<br/>whose duration expired"]
  tick --> due{"Due to read?<br/>every interval_seconds"}
  due -->|"Yes"| read["Read sensors · LCD display<br/>publish telemetry"]
  due -->|"No"| sleep["Wait 0.5 s"]
  read --> sleep --> mqtt
  poll -.->|"OSError"| lost["Disconnected: wait 10 s<br/>and reconnect"] -.-> mqtt
  classDef leaf fill:#DDF5EA,stroke:#067A52,color:#17261F
  classDef water fill:#E3F2FB,stroke:#1F6FA0,color:#17261F
  classDef sun fill:#FDF4DD,stroke:#C98D12,color:#17261F
  classDef clay fill:#FBE9E1,stroke:#B85A38,color:#17261F
  classDef core fill:#067A52,stroke:#0B3D2B,color:#FFFFFF
  classDef deep fill:#0B3D2B,stroke:#06281C,color:#FFFFFF
  classDef muted fill:#F2F7F4,stroke:#5B6B63,color:#17261F
  class boot core
  class wifi,mqtt,due sun
  class wait,sleep,lost muted
  class ntp,connect,poll,tick,read leaf
```

### 4. Commands

<!-- diagrama: SmartPot_IoT_02_Command_Handling | titulo=Cómo atiende una orden -->
```mermaid
%%{init: {"theme": "base", "fontFamily": "Segoe UI, Arial, sans-serif", "themeVariables": {"fontFamily": "Segoe UI, Arial, sans-serif", "fontSize": "15px", "primaryColor": "#DDF5EA", "primaryTextColor": "#17261F", "primaryBorderColor": "#067A52", "secondaryColor": "#E3F2FB", "secondaryTextColor": "#17261F", "secondaryBorderColor": "#1F6FA0", "tertiaryColor": "#F2F7F4", "tertiaryTextColor": "#17261F", "tertiaryBorderColor": "#D5E3DC", "lineColor": "#5B6B63", "textColor": "#17261F", "mainBkg": "#DDF5EA", "nodeBorder": "#067A52", "clusterBkg": "#F7FAF8", "clusterBorder": "#D5E3DC", "edgeLabelBackground": "#FFFFFF", "actorBkg": "#067A52", "actorBorder": "#0B3D2B", "actorTextColor": "#FFFFFF", "actorLineColor": "#5B6B63", "signalColor": "#17261F", "signalTextColor": "#17261F", "labelBoxBkgColor": "#0B3D2B", "labelBoxBorderColor": "#0B3D2B", "labelTextColor": "#FFFFFF", "loopTextColor": "#0B3D2B", "noteBkgColor": "#FDF4DD", "noteBorderColor": "#C98D12", "noteTextColor": "#17261F", "activationBkgColor": "#DDF5EA", "activationBorderColor": "#067A52", "attributeBackgroundColorOdd": "#FFFFFF", "attributeBackgroundColorEven": "#F2F7F4"}}}%%
sequenceDiagram
  autonumber
  participant A as SmartPot-API
  participant B as Broker
  participant C as SmartPotClient
  participant K as ActuatorBank
  A->>B: commands {id, actuator, action, durationSeconds} (QoS 1)
  B->>C: message on smartpot/v1/{cropId}/commands
  C->>C: parse_command validates payload
  C->>K: execute(command, now)
  alt Installed actuator
    K->>K: turns pin on or off
    K-->>C: EXECUTED · "Water pump turned on for 15 s"
  else Missing on device
    K-->>C: FAILED · "This device does not have HUMIDIFIER"
  end
  C->>B: commands/ack {id, status, message} (QoS 1)
  B->>A: confirms command
  Note over K: With durationSeconds, tick turns it off automatically<br/>when the timer expires
```

### 5. Secure Connection

| Parameter | Value |
| --- | --- |
| Broker | `mqtt.smartpot.app:8883`, MQTT over TLS 1.2 or newer |
| Certificate | Signed by SmartPot's internal CA; verified by the firmware using the public CA embedded in `config.py` under `BROKER["CA_CRT"]` |
| Username & Key | Crop ID and device key, shown once by the PWA upon creating the real crop or rotating the key |
| Client ID | `smartpot-<cropId>` |
| Status | Retained `online` upon connecting and `offline` as Last Will |

> [!WARNING]
> **Wokwi.** Keep your copy of the project private: anyone who sees `config.py` can publish as your crop. If leaked, generate a new key from the Device tab.

<!-- parte: PART III | Configuration & Testing -->

### 6. Configuration

The PWA generates `config.py` in its connection guide, containing the WiFi network (yours or `Wokwi-GUEST`), the crop's `SMARTPOT` block, and the `BROKER` block with the broker CA. `config.py` is untracked; `config.example.py` is the template.

| Key | Purpose |
| --- | --- |
| `WIFI.ssid`, `WIFI.password` | Network the ESP32 connects to |
| `SMARTPOT.crop_id`, `device_key` | MQTT credentials for the real crop |
| `SMARTPOT.host`, `port`, `tls` | Broker host, port, and TLS toggle |
| `BROKER.CA_CRT` | SmartPot public CA in PEM format used by the firmware to verify the broker certificate |
| `SMARTPOT.interval_seconds` | Seconds between readings (30 by default) |

### 7. Tests

`uv run ruff check .` and `uv run pytest`: CPython test suite with simulated MicroPython modules covering the MQTT contract, commands and ACKs (with durations in seconds, minutes, or hours), timed actuator shutoff, sensor scaling, broker verification with the template CA, and TLS fallback for older MicroPython versions.

### 8. Getting Started

| Environment | Steps |
| --- | --- |
| Physical board | Wire the circuit, flash MicroPython 1.23+, copy `fs/` with `mpremote`, add your `config.py` (includes the CA), and power on |
| Wokwi in browser | Open the project, paste your `config.py`, and run; the `Wokwi-GUEST` network has Internet access |
| Local Wokwi | `uv sync`, start the simulation with `wokwi.toml`, and run `uv run python start.py` |

---

<a id="español"></a>

## Español

### Ficha del documento

| Campo | Valor |
| --- | --- |
| Proyecto | SmartPot · [smartpot.app](https://smartpot.app) |
| Componente | [SmartPot-IoT](https://github.com/SmartPotTech/SmartPot-IoT) |
| Versión | 1.1 · octubre 2026 |
| Alcance | Circuito, módulos del firmware, ciclo principal, comandos, conexión segura, configuración y pruebas |
| Documentación de la plataforma | [Documentación técnica](https://github.com/SmartPotTech/.github/blob/main/docs/SmartPot_Technical_Documentation.md), [recorrido del proyecto](https://github.com/SmartPotTech/.github/blob/main/docs/SmartPot_Project_Journey.md), [ciclo de vida](https://github.com/SmartPotTech/.github/blob/main/docs/SmartPot_Software_Lifecycle.md) y [diagramas generales](https://github.com/SmartPotTech/.github/blob/main/docs/README.md#diagramas-generales) |
| Mantenimiento | Se genera desde `docs/` de este repositorio con las herramientas de `.github/docs/tools`; se actualiza con cada cambio del componente |

<!-- parte: PARTE I | El componente -->

### 1. Propósito

#### En palabras simples

SmartPot-IoT es el programa que corre en el dispositivo de un **cultivo real**: un ESP32 con sus sensores, sus actuadores y una pantalla. Mide, muestra, publica las lecturas en el broker de SmartPot y obedece las órdenes que llegan, confirmando cada una. No decide nada por su cuenta: la API y el asistente deciden. El mismo firmware corre en una placa física o en [Wokwi](https://wokwi.com/projects/408863167711709185), y en los dos casos el cultivo es real para la plataforma; los cultivos virtuales, en cambio, los simula SmartPot-DataGenerator.

### 2. Arquitectura del componente

| Componente | Pin ESP32 | Escala enviada |
| --- | --- | --- |
| DHT22 (temperatura y humedad del aire) | GPIO 15 | °C y % |
| Sensor de luz (potenciómetro en Wokwi) | GPIO 34 | 0–2000 lux |
| Sensor de pH | GPIO 35 | 0–14 |
| Sensor de TDS | GPIO 32 | 0–3000 ppm |
| Humedad del sustrato | GPIO 33 | 0–100 % |
| Bomba de agua | GPIO 19 | `WATER_PUMP` |
| Luz ultravioleta | GPIO 18 | `UV_LIGHT` |
| Ventilador | GPIO 5 | `FAN` |
| LCD 20×4 I2C | SCL 16 · SDA 17 | — |

<!-- parte: PARTE II | Funcionamiento -->

### 3. Ciclo principal

El ciclo se ejecuta cada 0,5 segundos con o sin red: apaga los actuadores cuya duración venció, lee sensores cada `interval_seconds`, actualiza la pantalla LCD y, cuando está conectado al broker, publica telemetría y atiende comandos.

### 4. Comandos

Recibe comandos JSON en `smartpot/v1/{cropId}/commands`, acciona los pines correspondientes en `ActuatorBank` y responde en `smartpot/v1/{cropId}/commands/ack` con estado `EXECUTED` o `FAILED` y mensaje en inglés.

### 5. Conexión segura

| Parámetro | Valor |
| --- | --- |
| Broker | `mqtt.smartpot.app:8883`, MQTT sobre TLS 1.2 o superior |
| Certificado | Firmado por la CA propia de SmartPot; el firmware lo verifica con la CA pública que trae `config.py` en `BROKER["CA_CRT"]` |
| Usuario y clave | El id del cultivo y la clave del dispositivo, que la PWA muestra una sola vez al crear el cultivo real o al rotarla |
| Client id | `smartpot-<cropId>` |
| Estado | `online` retenido al conectar y `offline` como última voluntad |

> [!WARNING]
> **Wokwi.** Deja tu copia del proyecto como privada: quien vea `config.py` puede publicar como tu cultivo. Si se filtra, genera una nueva clave desde la pestaña Dispositivo.

<!-- parte: PARTE III | Operación -->

### 6. Configuración

La PWA genera `config.py` en su guía de conexión, con la red WiFi (la tuya o `Wokwi-GUEST`), el bloque `SMARTPOT` del cultivo y el bloque `BROKER` con la CA del broker. `config.py` no se versiona; `config.example.py` es la plantilla.

| Clave | Uso |
| --- | --- |
| `WIFI.ssid`, `WIFI.password` | Red a la que se conecta el ESP32 |
| `SMARTPOT.crop_id`, `device_key` | Cuenta MQTT del cultivo real |
| `SMARTPOT.host`, `port`, `tls` | Broker y uso de TLS |
| `BROKER.CA_CRT` | CA pública de SmartPot en PEM; con ella el firmware verifica el certificado del broker |
| `SMARTPOT.interval_seconds` | Segundos entre lecturas (30 por defecto) |

### 7. Pruebas

`uv run ruff check .` y `uv run pytest`: pruebas con CPython y módulos de MicroPython simulados sobre el contrato MQTT, los comandos y su ACK, el apagado por tiempo de los actuadores, la escala de los sensores, la verificación del broker con la CA de la plantilla y el respaldo de TLS en versiones anteriores de MicroPython.

### 8. Puesta en marcha

| Dónde | Pasos |
| --- | --- |
| Placa física | Arma el circuito, graba MicroPython 1.23, copia `fs/` con `mpremote`, agrega tu `config.py` (ya trae la CA) y enciende |
| Wokwi en el navegador | Abre el proyecto, pega tu `config.py` y ejecuta; la red `Wokwi-GUEST` tiene salida a internet |
| Wokwi local | `uv sync`, inicia la simulación con `wokwi.toml` y ejecuta `uv run python start.py` |
