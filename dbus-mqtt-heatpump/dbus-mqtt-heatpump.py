#!/usr/bin/env python

from gi.repository import GLib  # pyright: ignore[reportMissingImports]
import platform
import logging
import sys
import os
from time import sleep, time
import json
import paho.mqtt.client as mqtt
import configparser  # for config/ini file
import _thread

# import Victron Energy packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), "ext", "velib_python"))
from vedbus import VeDbusService  # noqa: E402
from ve_utils import get_vrm_portal_id  # noqa: E402


# get values from config.ini file
try:
    config_file = (os.path.dirname(os.path.realpath(__file__))) + "/config.ini"
    if os.path.exists(config_file):
        config = configparser.ConfigParser()
        config.read(config_file)
        if config["MQTT"]["broker_address"] == "IP_ADDR_OR_FQDN":
            print('ERROR:The "config.ini" is using invalid default values like IP_ADDR_OR_FQDN. The driver restarts in 60 seconds.')
            sleep(60)
            sys.exit()
    else:
        print('ERROR:The "' + config_file + '" is not found. Did you copy or rename the "config.sample.ini" to "config.ini"? The driver restarts in 60 seconds.')
        sleep(60)
        sys.exit()

except Exception:
    exception_type, exception_object, exception_traceback = sys.exc_info()
    file = exception_traceback.tb_frame.f_code.co_filename
    line = exception_traceback.tb_lineno
    print(f"Exception occurred: {repr(exception_object)} of type {exception_type} in {file} line #{line}")
    print("ERROR:The driver restarts in 60 seconds.")
    sleep(60)
    sys.exit()


# Get logging level from config.ini
# ERROR = shows errors only
# WARNING = shows ERROR and warnings
# INFO = shows WARNING and running functions
# DEBUG = shows INFO and data/values
if "DEFAULT" in config and "logging" in config["DEFAULT"]:
    if config["DEFAULT"]["logging"] == "DEBUG":
        logging.basicConfig(level=logging.DEBUG)
    elif config["DEFAULT"]["logging"] == "INFO":
        logging.basicConfig(level=logging.INFO)
    elif config["DEFAULT"]["logging"] == "ERROR":
        logging.basicConfig(level=logging.ERROR)
    else:
        logging.basicConfig(level=logging.WARNING)
else:
    logging.basicConfig(level=logging.WARNING)


# get timeout
if "DEFAULT" in config and "timeout" in config["DEFAULT"]:
    timeout = int(config["DEFAULT"]["timeout"])
else:
    timeout = 60

# get power threshold
if "DEFAULT" in config and "power_threshold" in config["DEFAULT"]:
    power_threshold = int(config["DEFAULT"]["power_threshold"])
else:
    power_threshold = 230 * 63  # 230 V * 63 A — generous upper bound for a heatpump


# set variables
connected = 0
last_changed = 0
last_updated = 0

# -1 signals "not yet received"; None means "not provided in payload"
heatpump_power = -1
heatpump_current = None
heatpump_voltage = None
heatpump_forward = None
heatpump_temperature = None
heatpump_target_temperature = None
heatpump_state = None


# MQTT requests
def on_disconnect(client, userdata, rc):
    global connected
    logging.warning("MQTT client: Got disconnected")
    if rc != 0:
        logging.warning("MQTT client: Unexpected MQTT disconnection. Will auto-reconnect")
    else:
        logging.warning("MQTT client: rc value:" + str(rc))

    while connected == 0:
        try:
            logging.warning(f"MQTT client: Trying to reconnect to broker {config['MQTT']['broker_address']} on port {config['MQTT']['broker_port']}")
            client.connect(host=config["MQTT"]["broker_address"], port=int(config["MQTT"]["broker_port"]))
            connected = 1
        except Exception as err:
            logging.error(f"MQTT client: Error in retrying to connect with broker ({config['MQTT']['broker_address']}:{config['MQTT']['broker_port']}): {err}")
            logging.error("MQTT client: Retrying in 15 seconds")
            connected = 0
            sleep(15)


def on_connect(client, userdata, flags, rc):
    global connected
    if rc == 0:
        logging.info("MQTT client: Connected to MQTT broker!")
        connected = 1
        client.subscribe(config["MQTT"]["topic"])
    else:
        logging.error("MQTT client: Failed to connect, return code %d\n", rc)


def on_message(client, userdata, msg):
    try:
        global last_changed
        global heatpump_power, heatpump_current, heatpump_voltage, heatpump_forward
        global heatpump_temperature, heatpump_target_temperature, heatpump_state

        if msg.topic == config["MQTT"]["topic"]:
            if msg.payload != "" and msg.payload != b"":
                jsonpayload = json.loads(msg.payload)

                last_changed = int(time())

                if "heatpump" in jsonpayload:
                    hp = jsonpayload["heatpump"]
                    if not isinstance(hp, dict):
                        logging.error('Received JSON MQTT message: "heatpump" value is not a dictionary. Expected: {"heatpump": {"power": 0.0}}')
                        logging.debug("MQTT payload: " + str(msg.payload)[1:])
                        return

                    if "power" not in hp:
                        logging.error('Received JSON MQTT message does not include "power" in the heatpump object. Expected at least: {"heatpump": {"power": 0.0}}')
                        logging.debug("MQTT payload: " + str(msg.payload)[1:])
                        return

                    power = float(hp["power"])
                    if power < 0 or power > power_threshold:
                        logging.error(f"power is out of range: {power}. Allowed range is 0 to {power_threshold}")
                        logging.debug("MQTT payload: " + str(msg.payload)[1:])
                        return

                    heatpump_power = power
                    heatpump_voltage = float(hp["voltage"]) if "voltage" in hp else float(config["DEFAULT"]["voltage"])
                    heatpump_current = float(hp["current"]) if "current" in hp else (heatpump_power / heatpump_voltage if heatpump_voltage != 0 else 0)
                    heatpump_forward = float(hp["energy_forward"]) if "energy_forward" in hp else None

                    heatpump_temperature = float(hp["temperature"]) if "temperature" in hp else None
                    heatpump_target_temperature = float(hp["target_temperature"]) if "target_temperature" in hp else None

                    if "state" in hp:
                        state_val = int(hp["state"])
                        if state_val not in (0, 1, 2, 3, 4):
                            logging.warning(f"state value {state_val} is not a known VenusOS heatpump state (0=Off, 1=Error, 2=Startup, 3=Heating, 4=Cooling). Ignoring.")
                        else:
                            heatpump_state = state_val
                    else:
                        heatpump_state = None

                else:
                    logging.error('Received JSON MQTT message does not include a "heatpump" object. Expected at least: {"heatpump": {"power": 0.0}}')
                    logging.debug("MQTT payload: " + str(msg.payload)[1:])

            else:
                logging.warning("Received MQTT message was empty and therefore ignored")
                logging.debug("MQTT payload: " + str(msg.payload)[1:])

    except TypeError as e:
        logging.error("Received message is not valid. Check the README and sample payload. %s" % e)
        logging.debug("MQTT payload: " + str(msg.payload)[1:])

    except ValueError as e:
        logging.error("Received message is not a valid JSON. Check the README and sample payload. %s" % e)
        logging.debug("MQTT payload: " + str(msg.payload)[1:])

    except Exception:
        exception_type, exception_object, exception_traceback = sys.exc_info()
        file = exception_traceback.tb_frame.f_code.co_filename
        line = exception_traceback.tb_lineno
        print(f"Exception occurred: {repr(exception_object)} of type {exception_type} in {file} line #{line}")
        logging.debug("MQTT payload: " + str(msg.payload)[1:])


class DbusMqttHeatpumpService:
    def __init__(
        self,
        servicename,
        deviceinstance,
        paths,
        productname="MQTT Heat Pump",
        customname="MQTT Heat Pump",
        connection="MQTT Heat Pump service",
    ):
        self._dbusservice = VeDbusService(servicename, register=False)
        self._paths = paths

        logging.debug("%s /DeviceInstance = %d" % (servicename, deviceinstance))

        # Create the management objects, as specified in the ccgx dbus-api document
        self._dbusservice.add_path("/Mgmt/ProcessName", __file__)
        self._dbusservice.add_path(
            "/Mgmt/ProcessVersion",
            "Unknown version, and running on Python " + platform.python_version(),
        )
        self._dbusservice.add_path("/Mgmt/Connection", connection)

        # Create the mandatory objects
        self._dbusservice.add_path("/DeviceInstance", deviceinstance)
        self._dbusservice.add_path("/ProductId", 0xFFFF)
        self._dbusservice.add_path("/ProductName", productname)
        self._dbusservice.add_path("/CustomName", customname)
        self._dbusservice.add_path("/FirmwareVersion", "0.1.0 (20260516)")
        self._dbusservice.add_path("/Connected", 1)

        self._dbusservice.add_path("/Latency", None)

        for path, settings in self._paths.items():
            self._dbusservice.add_path(
                path,
                settings["initial"],
                gettextcallback=settings["textformat"],
                writeable=True,
                onchangecallback=self._handlechangedvalue,
            )

        # register VeDbusService after all paths were added
        self._dbusservice.register()

        GLib.timeout_add(1000, self._update)  # pause 1000ms before the next request

    def _update(self):
        global last_changed, last_updated

        now = int(time())

        try:
            if last_changed != last_updated:
                self._dbusservice["/Ac/Power"] = round(heatpump_power, 2) if heatpump_power is not None else None
                self._dbusservice["/Ac/Current"] = round(heatpump_current, 2) if heatpump_current is not None else None
                self._dbusservice["/Ac/Voltage"] = round(heatpump_voltage, 2) if heatpump_voltage is not None else None
                if heatpump_forward is not None:
                    self._dbusservice["/Ac/Energy/Forward"] = round(heatpump_forward, 2)
                if heatpump_temperature is not None:
                    self._dbusservice["/Temperature"] = round(heatpump_temperature, 1)
                if heatpump_target_temperature is not None:
                    self._dbusservice["/TargetTemperature"] = round(heatpump_target_temperature, 1)
                if heatpump_state is not None:
                    self._dbusservice["/State"] = heatpump_state

                logging.debug(
                    "Heat pump: {:.1f} W - {:.1f} V - {:.1f} A".format(
                        heatpump_power,
                        heatpump_voltage if heatpump_voltage is not None else 0,
                        heatpump_current if heatpump_current is not None else 0,
                    )
                )
                if heatpump_temperature is not None:
                    logging.debug("|- Temperature: {:.1f} °C".format(heatpump_temperature))
                if heatpump_target_temperature is not None:
                    logging.debug("|- Target temperature: {:.1f} °C".format(heatpump_target_temperature))
                if heatpump_state is not None:
                    state_names = {0: "Off", 1: "Error", 2: "Startup", 3: "Heating", 4: "Cooling"}
                    logging.debug("|- State: {} ({})".format(heatpump_state, state_names.get(heatpump_state, "Unknown")))

                last_updated = last_changed

        except Exception:
            exception_type, exception_object, exception_traceback = sys.exc_info()
            file = exception_traceback.tb_frame.f_code.co_filename
            line = exception_traceback.tb_lineno
            logging.error(f"Exception occurred: {repr(exception_object)} of type {exception_type} in {file} line #{line}")
            logging.error("ERROR:The driver restarts now.")
            sys.exit()

        # quit driver if timeout is exceeded
        if timeout != 0 and (now - last_changed) > timeout:
            logging.error("Driver stopped. Timeout of %i seconds exceeded, since no new MQTT message was received in this time." % timeout)
            sys.exit()

        # increment UpdateIndex - to show that new data is available
        index = self._dbusservice["/UpdateIndex"] + 1  # increment index
        if index > 255:  # maximum value of the index
            index = 0  # overflow from 255 to 0
        self._dbusservice["/UpdateIndex"] = index
        return True

    def _handlechangedvalue(self, path, value):
        logging.debug("someone else updated %s to %s" % (path, value))
        return True  # accept the change


def main():
    _thread.daemon = True  # allow the program to quit

    from dbus.mainloop.glib import (
        DBusGMainLoop,
    )  # pyright: ignore[reportMissingImports]

    # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
    DBusGMainLoop(set_as_default=True)

    # MQTT setup
    client_id = "MqttHeatpump_" + get_vrm_portal_id() + "_" + str(config["DEFAULT"]["device_instance"])
    try:
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    except AttributeError:
        client = mqtt.Client(client_id)
    client.on_disconnect = on_disconnect
    client.on_connect = on_connect
    client.on_message = on_message

    # check tls and use settings, if provided
    if "tls_enabled" in config["MQTT"] and config["MQTT"]["tls_enabled"] == "1":
        logging.info("MQTT client: TLS is enabled")

        if "tls_path_to_ca" in config["MQTT"] and config["MQTT"]["tls_path_to_ca"] != "":
            logging.info('MQTT client: TLS: custom ca "%s" used' % config["MQTT"]["tls_path_to_ca"])
            client.tls_set(config["MQTT"]["tls_path_to_ca"], tls_version=2)
        else:
            client.tls_set(tls_version=2)

        if "tls_insecure" in config["MQTT"] and config["MQTT"]["tls_insecure"] != "":
            logging.info("MQTT client: TLS certificate server hostname verification disabled")
            client.tls_insecure_set(True)

    # check if username and password are set
    if "username" in config["MQTT"] and "password" in config["MQTT"] and config["MQTT"]["username"] != "" and config["MQTT"]["password"] != "":
        logging.info('MQTT client: Using username "%s" and password to connect' % config["MQTT"]["username"])
        client.username_pw_set(username=config["MQTT"]["username"], password=config["MQTT"]["password"])

    # connect to broker
    logging.info(f"MQTT client: Connecting to broker {config['MQTT']['broker_address']} on port {config['MQTT']['broker_port']}")
    client.connect(host=config["MQTT"]["broker_address"], port=int(config["MQTT"]["broker_port"]))
    client.loop_start()

    # wait to receive first data
    i = 0
    while heatpump_power == -1:
        if i % 12 != 0 or i == 0:
            logging.info("Waiting 5 seconds for receiving first data...")
        else:
            logging.warning("Waiting since %s seconds for receiving first data..." % str(i * 5))

        # check if timeout was exceeded
        if timeout != 0 and timeout <= (i * 5):
            logging.error("Driver stopped. Timeout of %i seconds exceeded, since no new MQTT message was received in this time." % timeout)
            sys.exit()

        sleep(5)
        i += 1

    # formatting
    def _kwh(p, v):
        return str("%.2f" % v) + "kWh"

    def _a(p, v):
        return str("%.1f" % v) + "A"

    def _w(p, v):
        return str("%i" % v) + "W"

    def _v(p, v):
        return str("%.2f" % v) + "V"

    def _degc(p, v):
        return str("%.1f" % v) + "°C"

    def _n(p, v):
        return str("%i" % v)

    paths_dbus = {
        "/Ac/Power": {"initial": 0, "textformat": _w},
        "/Ac/Current": {"initial": 0, "textformat": _a},
        "/Ac/Voltage": {"initial": 0, "textformat": _v},
        "/Ac/Energy/Forward": {"initial": None, "textformat": _kwh},
        "/Temperature": {"initial": None, "textformat": _degc},
        "/TargetTemperature": {"initial": None, "textformat": _degc},
        "/State": {"initial": None, "textformat": _n},
        "/UpdateIndex": {"initial": 0, "textformat": _n},
    }

    device_instance = int(config["DEFAULT"]["device_instance"])
    device_name = config["DEFAULT"].get("device_name", "MQTT Heat Pump")

    DbusMqttHeatpumpService(
        servicename="com.victronenergy.heatpump.mqtt_heatpump_" + str(device_instance),
        deviceinstance=device_instance,
        customname=device_name,
        paths=paths_dbus,
    )

    logging.info("Connected to dbus and switching over to GLib.MainLoop() (= event based)")
    mainloop = GLib.MainLoop()
    mainloop.run()


if __name__ == "__main__":
    main()
