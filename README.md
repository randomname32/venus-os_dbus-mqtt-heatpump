# dbus-mqtt-heatpump

A VenusOS driver that subscribes to an MQTT topic and publishes heat pump data to the VenusOS dbus as a `com.victronenergy.heatpump` service. This makes any heat pump (or heat pump controller) with MQTT output visible in the VenusOS device list and VRM portal, including power consumption, temperatures, operating state, and consumed energy.

This driver is based on [venus-os_dbus-mqtt-grid](https://github.com/mr-manuel/venus-os_dbus-mqtt-grid) by [mr-manuel](https://github.com/mr-manuel). All credit for the original architecture and implementation goes to him.

---

## Features

- Publishes to `com.victronenergy.heatpump` on the VenusOS dbus
- Reads power, current, voltage, consumed energy, water/output temperature, setpoint temperature, and operating state from a single MQTT topic
- TLS and username/password authentication supported
- Configurable timeout and input validation
- Supports multiple parallel instances via the `device_instance` setting

---

## dbus paths written

| dbus path | Unit | Description |
|-----------|------|-------------|
| `/Ac/Power` | W | Current power consumption |
| `/Ac/Current` | A | Current draw (calculated from power/voltage if not provided) |
| `/Ac/Voltage` | V | AC voltage |
| `/Ac/Energy/Forward` | kWh | Cumulative energy consumed |
| `/Temperature` | °C | Measured temperature (e.g., water outlet) |
| `/TargetTemperature` | °C | Temperature setpoint |
| `/State` | — | Operating state (see table below) |

### State values

| Value | Meaning |
|-------|---------|
| 0 | Off |
| 1 | Error |
| 2 | Startup |
| 3 | Heating |
| 4 | Cooling |

---

## MQTT payload format

The driver subscribes to a single configurable MQTT topic and expects a JSON payload with a top-level `"heatpump"` object.

### Minimum required payload

```json
{"heatpump": {"power": 800.0}}
```

### Full payload (all optional fields included)

```json
{
  "heatpump": {
    "power": 800.0,
    "current": 3.48,
    "voltage": 230.0,
    "energy_forward": 1250.50,
    "temperature": 45.5,
    "target_temperature": 50.0,
    "state": 3
  }
}
```

### Field reference

| Field | Required | Type | Unit | Description |
|-------|----------|------|------|-------------|
| `power` | **yes** | float | W | Current power consumption. Must be ≥ 0. |
| `current` | no | float | A | Current draw. Calculated as `power / voltage` if omitted. |
| `voltage` | no | float | V | AC voltage. Falls back to `voltage` in `config.ini` if omitted. |
| `energy_forward` | no | float | kWh | Cumulative consumed energy (monotonically increasing). |
| `temperature` | no | float | °C | Measured temperature (e.g., water outlet or room sensor). |
| `target_temperature` | no | float | °C | Temperature setpoint. |
| `state` | no | int | — | Operating state. See state table above. |

---

## Installation

### 1. Copy the driver to VenusOS

Copy the `dbus-mqtt-heatpump` folder to `/data/etc/dbus-mqtt-heatpump` on your VenusOS device (e.g., Cerbo GX):

```bash
scp -r dbus-mqtt-heatpump root@<venus-ip>:/data/etc/
```

Alternatively you can use the `download.sh` script directly on the VenusOS device once a GitHub release is available:

```bash
wget -O /tmp/download.sh https://raw.githubusercontent.com/randomname32/venus-os_dbus-mqtt-heatpump/master/download.sh
bash /tmp/download.sh
```

### 2. Edit the config file

```bash
cp /data/etc/dbus-mqtt-heatpump/config.sample.ini /data/etc/dbus-mqtt-heatpump/config.ini
nano /data/etc/dbus-mqtt-heatpump/config.ini
```

Set at minimum:
- `broker_address` — IP or hostname of your MQTT broker
- `topic` — MQTT topic that carries the heat pump JSON payload

### 3. Run the install script

```bash
bash /data/etc/dbus-mqtt-heatpump/install.sh
```

This sets file permissions and registers the driver as a supervised service (via a symlink in `/service/`). The service starts automatically and survives firmware updates via `/data/rc.local`.

### 4. Verify

```bash
# watch the log
tail -f /var/log/dbus-mqtt-heatpump/current

# check if the service appears on dbus
dbus -y com.victronenergy.heatpump.mqtt_heatpump_100 /Ac/Power GetValue
```

---

## Configuration reference (`config.ini`)

| Key | Section | Default | Description |
|-----|---------|---------|-------------|
| `logging` | DEFAULT | `WARNING` | Log level: `ERROR`, `WARNING`, `INFO`, or `DEBUG` |
| `device_name` | DEFAULT | `MQTT Heat Pump` | Name shown in VenusOS and VRM |
| `device_instance` | DEFAULT | `100` | VRM device instance; must be unique per driver instance |
| `timeout` | DEFAULT | `60` | Seconds without an MQTT message before the driver exits. `0` disables. |
| `voltage` | DEFAULT | `230` | Fallback voltage used to calculate current when not in payload |
| `power_threshold` | DEFAULT | `14490` | Payloads with `power` above this value (W) are rejected |
| `broker_address` | MQTT | — | **Required.** IP or FQDN of the MQTT broker |
| `broker_port` | MQTT | `1883` | MQTT broker port |
| `tls_enabled` | MQTT | — | `1` to enable TLS |
| `tls_path_to_ca` | MQTT | — | Path to CA certificate for TLS |
| `tls_insecure` | MQTT | — | `1` to skip server hostname verification |
| `username` | MQTT | — | MQTT username |
| `password` | MQTT | — | MQTT password |
| `topic` | MQTT | — | **Required.** MQTT topic with the heat pump JSON payload |

---

## Running multiple instances

To run two heat pumps simultaneously, copy the driver folder twice and set different `device_instance` values in each `config.ini`:

```
/data/etc/dbus-mqtt-heatpump/       → device_instance = 100
/data/etc/dbus-mqtt-heatpump-2/     → device_instance = 101
```

Each folder needs its own `install.sh` run and its own MQTT topic.

---

## Uninstall

```bash
bash /data/etc/dbus-mqtt-heatpump/uninstall.sh
```

---

## License

MIT — see [LICENSE](LICENSE).
