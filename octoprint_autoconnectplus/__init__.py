# OctoPrint-AutoConnectPlus
#
# Automatically (re)connects the printer over serial, Moonraker or Bambu connectors.
#
# This plugin is a fork of OctoPrint-PortRetryPlus:
#   - Original work: hprombex (https://github.com/hprombex/OctoPrint-PortRetryPlus)
#   - Earlier work / inspiration: vehystrix
# The serial retry/timer logic is carried over from PortRetryPlus; the connector
# (Moonraker/Bambu) support is new.
#
# Maintainer of this fork: ajimaru
#
# Licensed under the GNU Affero General Public License v3 (AGPLv3), matching the
# original project. See the LICENSE file for details.

import serial

import octoprint.plugin
from octoprint.util import get_exception_string, RepeatedTimer

# Connection types supported by this plugin.
CONNECTION_SERIAL = "serial"
CONNECTION_MOONRAKER = "moonraker"
CONNECTION_BAMBU = "bambu"

# Connector-framework connection types (OctoPrint 2.0+).
CONNECTOR_TYPES = (CONNECTION_MOONRAKER, CONNECTION_BAMBU)


class AutoConnectPlusPlugin(
    octoprint.plugin.StartupPlugin,
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.TemplatePlugin,
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.EventHandlerPlugin,
):
    def __init__(self):
        super().__init__()
        self._serial_port = None

    # ------------------------------------------------------------------ #
    # Serial helpers (carried over from PortRetryPlus)
    # ------------------------------------------------------------------ #

    @property
    def serial_port(self) -> str:
        """
        Gets the serial port used by the system.

        If a serial port is already specified and not set to None or "AUTO", it returns
        the current serial port. Otherwise, it retrieves the serial port information
        from a global settings profile. If the retrieved port is still set to None or
        "AUTO", the configured forced port (if any) is used.

        Returns:
            str: The serial port in use.
        """
        # if we already have a serial port, return it
        if self._serial_port not in [None, "AUTO"]:
            return self._serial_port

        # otherwise, get the serial port from the printer profile
        self._serial_port = self._settings.global_get(["serial", "port"])

        # if we still don't have a serial port and we have a forced port, use it
        forced_port = self.__get_forced_port()
        if self._serial_port in [None, "AUTO"] and forced_port:
            self._serial_port = forced_port

        return self._serial_port

    # ------------------------------------------------------------------ #
    # Settings accessors
    # ------------------------------------------------------------------ #

    def __get_connection_type(self) -> str:
        connection_type = self._settings.get(["connection_type"])
        return connection_type if connection_type else CONNECTION_SERIAL

    def __get_interval(self) -> float:
        return self._settings.get_float(["interval"], min=0.1)

    def __get_forced_port(self) -> str:
        return self._settings.get(["forced_port"])

    def __get_connector_parameters(self, connection_type: str) -> dict:
        """Build the parameters dict for a connector-framework connect()."""
        if connection_type == CONNECTION_MOONRAKER:
            return {
                "host": self._settings.get(["moonraker", "host"]),
                "port": self._settings.get_int(["moonraker", "port"]) or 7125,
                "apikey": self._settings.get(["moonraker", "apikey"]),
            }
        if connection_type == CONNECTION_BAMBU:
            return {
                "host": self._settings.get(["bambu", "host"]),
                "serial": self._settings.get(["bambu", "serial"]),
                "access_code": self._settings.get(["bambu", "access_code"]),
            }
        return {}

    # ------------------------------------------------------------------ #
    # Timer machinery (carried over from PortRetryPlus)
    # ------------------------------------------------------------------ #

    def __timer_condition(self) -> bool:
        if not self._printer.is_closed_or_error():
            return False

        connection_type = self.__get_connection_type()

        if connection_type == CONNECTION_SERIAL:
            return self.serial_port not in [None, "AUTO"]

        if connection_type in CONNECTOR_TYPES:
            # require the essential params to be configured; no device node check
            params = self.__get_connector_parameters(connection_type)
            if connection_type == CONNECTION_MOONRAKER:
                return bool(params.get("host"))
            if connection_type == CONNECTION_BAMBU:
                return bool(
                    params.get("host")
                    and params.get("serial")
                    and params.get("access_code")
                )

        return False

    def __timer_cancelled(self):
        self._timer = None

    def __create_timer(self):
        if (not hasattr(self, "_timer")) or (self._timer is None):
            self._timer = RepeatedTimer(
                self.__get_interval(),
                self.do_auto_connect,
                condition=self.__timer_condition,
                on_finish=self.__timer_cancelled,
            )

    def __start_timer(self):
        self.__create_timer()
        self._timer.start()

    def __stop_timer(self):
        if self._timer:
            self._timer.cancel()

    # ------------------------------------------------------------------ #
    # Event / lifecycle hooks
    # ------------------------------------------------------------------ #

    def on_event(self, event: str, payload: dict):
        if not hasattr(self, "_timer"):
            return  # only occurs during server startup

        if "Connected" == event:
            self._logger.info("Printer connected, stopping timer")
            self.__stop_timer()
        elif "Disconnected" == event:
            self._logger.info("Printer disconnected, starting timer")
            self.__start_timer()

    def on_after_startup(self, *args, **kwargs):
        msg = (
            f"AutoConnectPlus starting with connection type "
            f"'{self.__get_connection_type()}' and interval {self.__get_interval()}"
        )
        if (
            self.__get_connection_type() == CONNECTION_SERIAL
            and self.__get_forced_port()
        ):
            msg += f" and forced serial port {self.__get_forced_port()}"
        self._logger.info(msg)
        self.__start_timer()

    def on_shutdown(self, *args, **kwargs):
        self.__stop_timer()

    # ------------------------------------------------------------------ #
    # Auto-connect dispatch
    # ------------------------------------------------------------------ #

    def do_auto_connect(self, *args, **kwargs):
        try:
            if not self._printer.is_closed_or_error():
                return

            connection_type = self.__get_connection_type()

            printer_profile = self._printer_profile_manager.get_default()
            profile = printer_profile["id"] if "id" in printer_profile else "_default"

            if connection_type == CONNECTION_SERIAL:
                self.__connect_serial(profile)
            elif connection_type in CONNECTOR_TYPES:
                self.__connect_connector(connection_type, profile)
            else:
                self._logger.warning(
                    f"Unknown connection type '{connection_type}', skipping"
                )
        except Exception:
            self._logger.error(f"Exception in do_auto_connect {get_exception_string()}")

    def __connect_serial(self, profile: str):
        """Legacy serial auto-connect (PortRetryPlus behaviour)."""
        if self.serial_port in [None, "AUTO"]:
            return

        baudrate = self._settings.global_get_int(["serial", "baudrate"])
        portopen = False

        # try the serial port
        try:
            if type(baudrate) == int:
                self._logger.debug(f"using baudrate {baudrate}")
                ser0 = serial.Serial(self.serial_port, baudrate)
            else:
                self._logger.debug("using default baudrate")
                ser0 = serial.Serial(self.serial_port)
            portopen = ser0.is_open
        except serial.SerialException:
            self._logger.debug(f"Failed to open port {self.serial_port}")

        if portopen:
            self._logger.info(
                f"Attempting to connect to {self.serial_port} with profile {profile}"
            )
            self._printer.connect(port=self.serial_port, profile=profile)

    def __connect_connector(self, connection_type: str, profile: str):
        """Auto-connect via the OctoPrint 2.0 connector framework."""
        try:
            from octoprint.printer.connection import ConnectedPrinter
        except ImportError:
            self._logger.error(
                f"Connection type '{connection_type}' requires the OctoPrint 2.0 "
                "connector framework, which is not available on this OctoPrint "
                "version. Falling back to no-op. Use 'serial' on older OctoPrint."
            )
            return

        parameters = self.__get_connector_parameters(connection_type)

        # optional precondition check (e.g. host resolvable) before attempting
        connector_cls = ConnectedPrinter.find(connection_type)
        if connector_cls is None:
            self._logger.error(
                f"No connector registered for '{connection_type}'. Is the "
                f"corresponding connector plugin installed?"
            )
            return

        try:
            if not connector_cls.connection_preconditions_met(parameters):
                self._logger.debug(
                    f"Preconditions for '{connection_type}' not met "
                    f"(host unreachable or parameters incomplete), skipping"
                )
                return
        except Exception:
            # precondition check is best-effort; proceed if it errors out
            self._logger.debug(
                f"Precondition check for '{connection_type}' raised, "
                f"attempting connect anyway"
            )

        self._logger.info(
            f"Attempting to connect via '{connection_type}' with profile {profile}"
        )
        self._printer.connect(
            connector=connection_type, parameters=parameters, profile=profile
        )

    # ------------------------------------------------------------------ #
    # SettingsPlugin / AssetPlugin / TemplatePlugin
    # ------------------------------------------------------------------ #

    def get_settings_defaults(self, *args, **kwargs):
        return dict(
            connection_type=CONNECTION_SERIAL,
            interval=5.0,
            forced_port="",
            moonraker=dict(host="", port=7125, apikey=""),
            bambu=dict(host="", serial="", access_code=""),
        )

    def get_template_configs(self, *args, **kwargs):
        return [dict(type="settings", custom_bindings=False)]

    def get_assets(self, *args, **kwargs):
        return dict(js=["js/autoconnectplus.js"])

    def get_update_information(self, *args, **kwargs):
        return dict(
            autoconnectplus=dict(
                displayName=self._plugin_name,
                displayVersion=self._plugin_version,
                # use github release method of version check
                type="github_release",
                user="ajimaru",
                repo="OctoPrint-AutoConnectPlus",
                current=self._plugin_version,
                # update method: pip
                pip="https://github.com/ajimaru/OctoPrint-AutoConnectPlus/archive/{target}.zip",
            )
        )

    def on_settings_save(self, data):
        interval = self._settings.get_float(["interval"], min=0.1)

        octoprint.plugin.SettingsPlugin.on_settings_save(self, data)

        new_interval = self._settings.get_float(["interval"], min=0.1)
        if interval != new_interval:
            self._logger.info(f"Retry interval changed to {new_interval}")
            self.__stop_timer()
            self.__start_timer()


__plugin_name__ = "AutoConnectPlus"
__plugin_pythoncompat__ = ">=3,<4"


def __plugin_load__():
    global __plugin_implementation__
    plugin = AutoConnectPlusPlugin()
    __plugin_implementation__ = plugin

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": plugin.get_update_information,
    }
