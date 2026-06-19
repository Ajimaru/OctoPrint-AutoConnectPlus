# OctoPrint-AutoConnectPlus
#
# Fork of OctoPrint-PortRetryPlus (hprombex, inspired by vehystrix). Serial
# retry/timer logic carried over; Moonraker/Bambu connector support is new.
# Maintainer: ajimaru. Licensed AGPL-3.0-or-later (see LICENSE).
"""AutoConnectPlus OctoPrint plugin.

Auto-(re)connects the printer over serial, Moonraker or Bambu connectors,
retrying on a configurable interval with exponential backoff.
"""

import logging
import socket
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import Any, Dict, List, Optional, Protocol

import serial

import octoprint.plugin
from octoprint.printer import PrinterInterface
from octoprint.printer.profile import PrinterProfileManager
from octoprint.util import RepeatedTimer, get_exception_string

# Connector framework only exists on OctoPrint 2.0+; on older versions the
# import fails and we fall back to serial-only behaviour.
try:
    from octoprint.printer.connection import ConnectedPrinter
except ImportError:
    ConnectedPrinter = None


class _PluginSettings(Protocol):
    """Subset of OctoPrint's PluginSettings this plugin calls.

    PluginSettings resolves methods dynamically via __getattr__ (no static
    signatures), so this Protocol gives the type checker a real contract.
    """

    def get(self, path: List[str], **kwargs: Any) -> Any: ...
    def get_float(self, path: List[str], **kwargs: Any) -> float: ...
    def get_boolean(self, path: List[str], **kwargs: Any) -> bool: ...
    def global_get(self, path: List[str], **kwargs: Any) -> Any: ...
    def global_get_int(self, path: List[str], **kwargs: Any) -> int: ...


# Serial connector name (OctoPrint 2.0; implicit type on older versions).
CONNECTOR_SERIAL = "serial"

# OctoPrint 2.0 settings paths for the last/preferred connection. Read from
# here instead of duplicating connection data in the plugin's own settings.
PREFERRED_CONNECTOR_PATH = ["printerConnection", "preferred", "connector"]
PREFERRED_PARAMETERS_PATH = ["printerConnection", "preferred", "parameters"]

# Max timer ticks to skip between attempts on repeated failure. With the
# default 5s interval this caps the retry delay at ~80s.
MAX_BACKOFF_TICKS = 15

# Timeout (s) for the TCP reachability probe before a network connect(). Short
# so an unreachable host does not stall the retry timer.
REACHABILITY_TIMEOUT = 1.5

# Default TCP port to probe per connector when parameters lack one (moonraker:
# HTTP/API 7125, bambu: MQTT/TLS 8883). Connectors not listed skip the probe.
CONNECTOR_DEFAULT_PORTS = {
    "moonraker": 7125,
    "bambu": 8883,
}

# Read-only labels shown in settings so the user sees the picked-up connection.
# Unknown connectors fall back to their raw name.
CONNECTOR_LABELS = {
    CONNECTOR_SERIAL: "Serial",
    "moonraker": "Moonraker (Klipper)",
    "bambu": "Bambu",
}


class AutoConnectPlusPlugin(
    octoprint.plugin.StartupPlugin,
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.TemplatePlugin,
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.EventHandlerPlugin,
):
    """Auto-(re)connect the printer over serial or a 2.0 connector."""

    # Injected by OctoPrint's plugin mixins at runtime; declared here so the
    # type checker sees the correct (non-None) types.
    _settings: _PluginSettings
    _printer: PrinterInterface
    _printer_profile_manager: PrinterProfileManager
    _logger: logging.Logger
    _plugin_name: str
    _plugin_version: str

    def __init__(self):
        super().__init__()
        self._serial_port: Optional[str] = None
        self._timer: Optional[RepeatedTimer] = None
        # keys already logged at error level, to avoid spamming on every retry
        self._warned_keys = set()
        # consecutive failed attempts since the last success; drives backoff
        self._failures = 0
        # timer ticks to skip before the next attempt (backoff)
        self._skip_ticks = 0

    # ------------------------------------------------------------------ #
    # Serial helpers (carried over from PortRetryPlus)
    # ------------------------------------------------------------------ #

    @property
    def serial_port(self) -> Optional[str]:
        """Resolve the serial port to use.

        Returns the current port if set and not "AUTO"; otherwise reads the
        global serial.port, falling back to the configured forced port.
        """
        if self._serial_port not in [None, "AUTO"]:
            return self._serial_port

        self._serial_port = self._settings.global_get(["serial", "port"])

        forced_port = self.__get_forced_port()
        if self._serial_port in [None, "AUTO"] and forced_port:
            self._serial_port = forced_port

        return self._serial_port

    def __is_enabled(self) -> bool:
        return self._settings.get_boolean(["enabled"])

    def __get_interval(self) -> float:
        return self._settings.get_float(["interval"], min=0.1)

    def __get_forced_port(self) -> str:
        return self._settings.get(["forced_port"])

    def __get_preferred_connector(self) -> str:
        """Connector OctoPrint last used (2.0). Defaults to serial, also when
        the key is absent on serial-only versions."""
        connector = self._settings.global_get(PREFERRED_CONNECTOR_PATH)
        return connector if connector else CONNECTOR_SERIAL

    def __get_preferred_parameters(self) -> dict:
        """Return the parameters OctoPrint stored for the preferred conn."""
        params = self._settings.global_get(PREFERRED_PARAMETERS_PATH)
        return params if isinstance(params, dict) else {}

    def __is_serial_connector(self, connector: Optional[str]) -> bool:
        return connector in (None, "", CONNECTOR_SERIAL)

    def __detected_connection(self) -> Dict[str, str]:
        """Describe the connection to reconnect, for the settings display:
        label, target (serial port or host:port) and an optional warning."""
        connector = self.__get_preferred_connector()
        label = CONNECTOR_LABELS.get(connector, connector)

        if self.__is_serial_connector(connector):
            port = self.serial_port
            target = "" if port in (None, "AUTO") else str(port)
            warning = "" if target else (
                "No serial port detected yet; set one in OctoPrint's "
                "connection dialog or configure a forced port below."
            )
            return {"label": label, "target": target, "warning": warning}

        parameters = self.__get_preferred_parameters()
        host = parameters.get("host", "")
        port = parameters.get("port") or CONNECTOR_DEFAULT_PORTS.get(connector)
        target = f"{host}:{port}" if host and port else host

        warning = ""
        if not host:
            warning = (
                "No preferred connection stored. Connect once via OctoPrint's "
                "connection dialog so AutoConnectPlus knows what to reconnect."
            )
        elif ConnectedPrinter is not None and (
            ConnectedPrinter.find(connector) is None
        ):
            warning = (
                f"Connector '{connector}' is not installed. Install the "
                "matching connector plugin."
            )

        return {"label": label, "target": target, "warning": warning}

    def __warn_once(self, key: str, msg: str):
        """Log msg at error level the first time per key, debug thereafter, so
        a repeatedly failing connector doesn't flood the log. Reset on connect.
        """
        if key in self._warned_keys:
            self._logger.debug(msg)
        else:
            self._warned_keys.add(key)
            self._logger.error(msg)

    def __reset_failures(self):
        """Reset the warning/backoff state (called once the printer conn)."""
        self._warned_keys.clear()
        self._failures = 0
        self._skip_ticks = 0

    def __register_failure(self):
        """Record a failed attempt and grow the backoff: each consecutive
        failure skips one more tick, capped at MAX_BACKOFF_TICKS."""
        self._failures += 1
        self._skip_ticks = min(self._failures, MAX_BACKOFF_TICKS)

    def __timer_condition(self) -> bool:
        if not self._printer.is_closed_or_error():
            return False

        if self._skip_ticks > 0:
            self._skip_ticks -= 1
            return False

        connector = self.__get_preferred_connector()

        if self.__is_serial_connector(connector):
            # serial needs a resolvable port (global serial.port or forced)
            return self.serial_port not in [None, "AUTO"]

        # other connectors: keep retrying; precondition check happens in
        # do_auto_connect right before connecting
        return True

    def __timer_cancelled(self):
        self._timer = None

    def __start_timer(self):
        if not self.__is_enabled():
            return

        # RepeatedTimer is a Thread and can start only once; never re-start a
        # running one. Guards against Disconnected and on_after_startup both
        # arming the timer at startup.
        if self._timer is not None:
            return
        self._timer = RepeatedTimer(
            self.__get_interval(),
            self.do_auto_connect,
            condition=self.__timer_condition,
            on_finish=self.__timer_cancelled,
        )
        self._timer.start()

    def __stop_timer(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def on_event(self, event: str, payload: dict):
        if "Connected" == event:
            self._logger.info("Printer connected, stopping timer")
            self.__reset_failures()
            self.__stop_timer()
        elif "Disconnected" == event:
            self._logger.info("Printer disconnected, starting timer")
            self.__start_timer()

    def on_after_startup(self):
        """Log the active configuration and start the retry timer."""
        connector = self.__get_preferred_connector()
        msg = (
            f"AutoConnectPlus starting (preferred connector '{connector}', "
            f"interval {self.__get_interval()})"
        )
        if self.__is_serial_connector(connector) and self.__get_forced_port():
            msg += f" with forced serial port {self.__get_forced_port()}"
        self._logger.info(msg)
        self.__start_timer()

    def on_shutdown(self):
        """Stop the retry timer on server shutdown."""
        self.__stop_timer()

    def do_auto_connect(self):
        """Attempt a connection via the preferred connector, with backoff."""
        try:
            if not self._printer.is_closed_or_error():
                return

            connector = self.__get_preferred_connector()

            printer_profile = self._printer_profile_manager.get_default()
            profile = (
                printer_profile["id"]
                if "id" in printer_profile
                else "_default"
            )

            if self.__is_serial_connector(connector):
                attempted = self.__connect_serial(profile)
            else:
                attempted = self.__connect_connector(connector, profile)

            # If a connect() fired, schedule a backoff; the "Connected" event
            # resets it on success, else attempts back off progressively.
            if attempted:
                self.__register_failure()
        except Exception:  # pylint: disable=broad-exception-caught
            self._logger.error(
                f"Exception in do_auto_connect {get_exception_string()}"
            )
            self.__register_failure()

    def __connect_serial(self, profile: str) -> bool:
        """Legacy serial auto-connect (PortRetryPlus). Returns True if a
        connect() was attempted, False while merely waiting for the port."""
        if self.serial_port in [None, "AUTO"]:
            return False

        baudrate = self._settings.global_get_int(["serial", "baudrate"])
        portopen = False

        try:
            if isinstance(baudrate, int):
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
                f"Attempting to connect to {self.serial_port} "
                f"with profile {profile}"
            )
            self._printer.connect(port=self.serial_port, profile=profile)
            return True

        return False

    def __connect_connector(self, connector: str, profile: str) -> bool:
        """Auto-connect via the OctoPrint 2.0 connector framework (moonraker,
        bambu, ...). Parameters come from the stored preferred connection so
        they match what the user configured.

        Returns True if a connect() was attempted (or a persistent error
        warrants backoff), False if we are simply waiting.
        """
        if ConnectedPrinter is None:
            self.__warn_once(
                "no_framework",
                f"Connector '{connector}' requires the OctoPrint 2.0 "
                "connector framework, which is not available on this "
                "OctoPrint version. Skipping.",
            )
            return True  # persistent: back off

        connector_cls = ConnectedPrinter.find(connector)
        if connector_cls is None:
            self.__warn_once(
                f"no_connector:{connector}",
                f"No connector registered for '{connector}'. Is the "
                f"corresponding connector plugin installed?",
            )
            return True  # persistent: back off

        parameters = self.__get_preferred_parameters()

        # optional precondition check (best-effort; proceed if it errors out)
        try:
            if not connector_cls.connection_preconditions_met(parameters):
                self._logger.debug(
                    f"Preconditions for '{connector}' not met "
                    f"(host unreachable or parameters incomplete), skipping"
                )
                return False
        except Exception:  # pylint: disable=broad-exception-caught
            self._logger.debug(
                f"Precondition check for '{connector}' raised, "
                f"attempting connect anyway"
            )

        # Connector preconditions only resolve the hostname, so a literal IP
        # passes even when the printer is off and connect() then floods the log
        # with "No route to host". Probe the TCP port first and just wait if
        # unreachable, keeping the retry quiet.
        if not self.__host_reachable(connector, parameters):
            self._logger.debug(
                f"Host for '{connector}' not reachable yet, waiting"
            )
            return False

        self._logger.info(
            f"Attempting to connect via '{connector}' with profile {profile}"
        )
        self._printer.connect(
            connector=connector, parameters=parameters, profile=profile
        )
        return True

    def __host_reachable(self, connector: str, parameters: dict) -> bool:
        """TCP probe for the connector's host. Returns True ("go ahead") when
        reachable, or when no host/port is known (unknown connectors stay
        optimistic). Only a refused or timed-out host returns False.
        """
        host = parameters.get("host")
        if not host:
            return True

        port = parameters.get("port") or CONNECTOR_DEFAULT_PORTS.get(connector)
        if port is None:
            return True  # no port to probe; don't block the connect

        try:
            port = int(port)
        except (TypeError, ValueError):
            return True

        try:
            with socket.create_connection(
                (host, port), timeout=REACHABILITY_TIMEOUT
            ):
                return True
        except OSError:
            return False

    def get_settings_defaults(self):
        return dict(
            enabled=True,
            interval=5.0,
            forced_port="",
        )

    def get_settings_version(self):  # type: ignore[override]
        # Bump when the settings schema changes; pair with on_settings_migrate.
        return 1

    def on_settings_migrate(self, target, current):
        # No migrations yet (v1 is the initial schema). Add per-version steps
        # here when bumping get_settings_version.
        pass

    def get_template_configs(self):
        return [dict(type="settings", custom_bindings=False)]

    def is_template_autoescaped(self):  # type: ignore[override]
        # All expressions are plain text; nothing injects HTML, so autoescaping
        # is safe and silences OctoPrint's autoescape warning.
        return True

    def get_template_vars(self):
        # Exposed read-only as plugin_autoconnectplus_detected_*; evaluated
        # when the settings dialog is rendered.
        detected = self.__detected_connection()
        return {
            "detected_label": detected["label"],
            "detected_target": detected["target"],
            "detected_warning": detected["warning"],
        }

    def get_assets(self):
        return dict(js=["js/autoconnectplus.js"])

    def get_update_information(self):
        """Provide the softwareupdate check configuration for this plugin."""
        return dict(
            autoconnectplus=dict(
                displayName=self._plugin_name,
                displayVersion=self._plugin_version,
                # use github release method of version check
                type="github_release",
                user="ajimaru",
                repo="OctoPrint-AutoConnectPlus",
                current=self._plugin_version,
                pip=(
                    "https://github.com/ajimaru/OctoPrint-AutoConnectPlus"
                    "/archive/{target}.zip"
                ),
            )
        )

    def on_settings_save(self, data) -> Dict[Any, Any]:
        enabled = self.__is_enabled()
        interval = self.__get_interval()

        result = octoprint.plugin.SettingsPlugin.on_settings_save(self, data)

        new_enabled = self.__is_enabled()
        new_interval = self.__get_interval()

        if enabled != new_enabled:
            if new_enabled:
                self._logger.info("AutoConnect enabled, starting timer")
                if self._printer.is_closed_or_error():
                    self.__start_timer()
            else:
                self._logger.info("AutoConnect disabled, stopping timer")
                self.__stop_timer()
        elif new_enabled and interval != new_interval:
            self._logger.info(f"Retry interval changed to {new_interval}")
            self.__stop_timer()
            self.__start_timer()

        return result


__plugin_name__ = "AutoConnectPlus"
# Match the entry-point key so the runtime identifier is explicit (otherwise
# defaults to the package name).
__plugin_identifier__ = "autoconnectplus"
__plugin_author__ = "ajimaru"
__plugin_description__ = (
    "Automatically reconnects the printer over serial, Moonraker or Bambu "
    "connectors"
)
__plugin_url__ = "https://github.com/ajimaru/OctoPrint-AutoConnectPlus"
__plugin_license__ = "AGPL-3.0-or-later"
__plugin_pythoncompat__ = ">=3.9,<4"

# Single source of truth is pyproject.toml; read it back from the installed
# package metadata so the version is never duplicated by hand.
try:
    __plugin_version__ = _pkg_version("OctoPrint-AutoConnectPlus")
except PackageNotFoundError:
    __plugin_version__ = "0.0.0+unknown"

__plugin_implementation__ = None
__plugin_hooks__ = None


def __plugin_load__():
    plugin = AutoConnectPlusPlugin()

    # Populate the module-level names OctoPrint looks up after loading
    # (globals() avoids a `global` statement).
    module_globals = globals()
    module_globals["__plugin_implementation__"] = plugin
    module_globals["__plugin_hooks__"] = {
        "octoprint.plugin.softwareupdate.check_config":
            plugin.get_update_information,
    }
