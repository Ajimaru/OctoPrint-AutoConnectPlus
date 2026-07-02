# OctoPrint-AutoConnectPlus
#
# Fork of OctoPrint-PortRetryPlus (hprombex, inspired by vehystrix). Serial
# retry/timer logic carried over; Moonraker/Bambu connector support is new.
# Maintainer: ajimaru. Licensed AGPL-3.0-or-later (see LICENSE).
"""AutoConnectPlus OctoPrint plugin.

Auto-(re)connects the printer over serial, Moonraker or Bambu connectors,
retrying on a configurable interval with exponential backoff.
"""

from __future__ import annotations

import logging
import os
import socket
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING, Any, Protocol

import flask
import octoprint.plugin
from octoprint.events import Events
from octoprint.util import RepeatedTimer, get_exception_string
from serial.tools import list_ports

if TYPE_CHECKING:
    from octoprint.printer import PrinterInterface
    from octoprint.printer.profile import PrinterProfileManager

# Connector framework only exists on OctoPrint 2.0+; on older versions the
# import fails and we fall back to serial-only behaviour. The filemanager
# import works around a circular import in OctoPrint 2.0.0rc* (importing
# octoprint.printer.* before octoprint.filemanager trips the cycle); it is
# harmless on versions where the cycle is fixed or OctoPrint already runs.
try:
    import octoprint.filemanager  # noqa: F401
    from octoprint.printer.connection import ConnectedPrinter
except ImportError:
    ConnectedPrinter = None


class _PluginSettings(Protocol):
    """Subset of OctoPrint's PluginSettings this plugin calls.

    PluginSettings resolves methods dynamically via __getattr__ (no static
    signatures), so this Protocol gives the type checker a real contract.
    """

    def get(self, path: list[str], **kwargs: Any) -> Any: ...
    def get_float(self, path: list[str], **kwargs: Any) -> float: ...
    def get_boolean(self, path: list[str], **kwargs: Any) -> bool: ...
    def global_get(self, path: list[str], **kwargs: Any) -> Any: ...


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
    octoprint.plugin.ShutdownPlugin,
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.TemplatePlugin,
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.SimpleApiPlugin,
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
        self._timer: RepeatedTimer | None = None
        # keys already logged at error level, to avoid spamming on every retry
        self._warned_keys = set()
        # consecutive failed attempts since the last success; drives backoff
        self._failures = 0
        # timer ticks to skip before the next attempt (backoff)
        self._skip_ticks = 0

    # ------------------------------------------------------------------ #
    # Settings accessors
    # ------------------------------------------------------------------ #

    def _is_enabled(self) -> bool:
        return self._settings.get_boolean(["enabled"])

    def _get_interval(self) -> float:
        return self._settings.get_float(["interval"], min=0.1)

    def _get_forced_port(self) -> str:
        return self._settings.get(["forced_port"])

    def _get_preferred_connector(self) -> str:
        """Connector OctoPrint last used (2.0). Defaults to serial, also when
        the key is absent on serial-only versions."""
        connector = self._settings.global_get(PREFERRED_CONNECTOR_PATH)
        return connector if connector else CONNECTOR_SERIAL

    def _get_preferred_parameters(self) -> dict:
        """Return the parameters OctoPrint stored for the preferred conn."""
        params = self._settings.global_get(PREFERRED_PARAMETERS_PATH)
        return params if isinstance(params, dict) else {}

    def _is_serial_connector(self, connector: str | None) -> bool:
        return connector in (None, "", CONNECTOR_SERIAL)

    # ------------------------------------------------------------------ #
    # Serial helpers (carried over from PortRetryPlus)
    # ------------------------------------------------------------------ #

    @property
    def serial_port(self) -> str | None:
        """Resolve the serial port to use, re-read on every call so changes
        in OctoPrint's connection settings are picked up immediately.

        Returns the global serial.port if set and not "AUTO", otherwise the
        configured forced port, otherwise None (nothing to connect to yet).
        """
        port = self._settings.global_get(["serial", "port"])
        if port not in (None, "AUTO"):
            return port

        return self._get_forced_port() or None

    # ------------------------------------------------------------------ #
    # Failure tracking / backoff
    # ------------------------------------------------------------------ #

    def _warn_once(self, key: str, msg: str):
        """Log msg at error level the first time per key, debug thereafter, so
        a repeatedly failing connector doesn't flood the log. Reset on connect.
        """
        if key in self._warned_keys:
            self._logger.debug(msg)
        else:
            self._warned_keys.add(key)
            self._logger.error(msg)

    def _reset_failures(self):
        """Reset the warning/backoff state (called once the printer conn)."""
        self._warned_keys.clear()
        self._failures = 0
        self._skip_ticks = 0

    def _register_failure(self):
        """Record a failed attempt and grow the backoff: each consecutive
        failure skips one more tick, capped at MAX_BACKOFF_TICKS."""
        self._failures += 1
        self._skip_ticks = min(self._failures, MAX_BACKOFF_TICKS)

    # ------------------------------------------------------------------ #
    # Retry timer
    # ------------------------------------------------------------------ #

    def _timer_condition(self) -> bool:
        if not self._printer.is_closed_or_error():
            return False

        if self._skip_ticks > 0:
            self._skip_ticks -= 1
            return False

        connector = self._get_preferred_connector()

        if self._is_serial_connector(connector):
            # serial needs a resolvable port (global serial.port or forced)
            return self.serial_port is not None

        # other connectors: keep retrying; precondition check happens in
        # do_auto_connect right before connecting
        return True

    def _on_timer_finished(self):
        self._timer = None

    def _start_timer(self):
        if not self._is_enabled():
            return

        # RepeatedTimer is a Thread and can start only once; never re-start a
        # running one. Guards against Disconnected and on_after_startup both
        # arming the timer at startup.
        if self._timer is not None:
            return
        self._timer = RepeatedTimer(
            self._get_interval(),
            self.do_auto_connect,
            condition=self._timer_condition,
            on_finish=self._on_timer_finished,
        )
        self._timer.start()

    def _stop_timer(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None

    # ------------------------------------------------------------------ #
    # OctoPrint lifecycle hooks
    # ------------------------------------------------------------------ #

    def on_event(self, event: str, payload: dict):
        if event == Events.CONNECTED:
            self._logger.info("Printer connected, stopping timer")
            self._reset_failures()
            self._stop_timer()
        elif event == Events.DISCONNECTED:
            self._logger.info("Printer disconnected, starting timer")
            self._start_timer()

    def on_after_startup(self):
        """Log the active configuration and start the retry timer."""
        connector = self._get_preferred_connector()
        msg = (
            f"AutoConnectPlus starting (preferred connector '{connector}', "
            f"interval {self._get_interval()})"
        )
        if self._is_serial_connector(connector) and self._get_forced_port():
            msg += f" with forced serial port {self._get_forced_port()}"
        self._logger.info(msg)
        self._start_timer()

    def on_shutdown(self):
        """Stop the retry timer on server shutdown."""
        self._stop_timer()

    # ------------------------------------------------------------------ #
    # Connecting
    # ------------------------------------------------------------------ #

    def do_auto_connect(self):
        """Attempt a connection via the preferred connector, with backoff."""
        try:
            if not self._printer.is_closed_or_error():
                return

            connector = self._get_preferred_connector()

            printer_profile = self._printer_profile_manager.get_default()
            profile = printer_profile.get("id", "_default")

            if self._is_serial_connector(connector):
                attempted = self._connect_serial(profile)
            else:
                attempted = self._connect_connector(connector, profile)

            # If a connect() fired, schedule a backoff; the "Connected" event
            # resets it on success, else attempts back off progressively.
            if attempted:
                self._register_failure()
        except Exception:  # pylint: disable=broad-exception-caught
            self._logger.error(
                f"Exception in do_auto_connect {get_exception_string()}"
            )
            self._register_failure()

    def _connect_serial(self, profile: str) -> bool:
        """Legacy serial auto-connect (PortRetryPlus). Returns True if a
        connect() was attempted, False while merely waiting for the port."""
        port = self.serial_port
        if port is None:
            return False

        if not self._serial_port_present(port):
            self._logger.debug(f"Port {port} not present yet, waiting")
            return False

        self._logger.info(
            f"Attempting to connect to {port} with profile {profile}"
        )
        self._printer.connect(port=port, profile=profile)
        return True

    def _serial_port_present(self, port: str) -> bool:
        """Check that the port exists without opening it: opening a serial
        port toggles DTR, which resets Arduino-style boards, so an open-probe
        would reset the printer twice (probe + OctoPrint's own connect).

        Stays optimistic when enumeration fails; a truly bad port then just
        fails OctoPrint's connect() and the retry backs off.
        """
        try:
            available = {info.device for info in list_ports.comports()}
        except Exception:  # pylint: disable=broad-exception-caught
            return True

        if port in available:
            return True

        # Forced ports are often stable symlinks (/dev/serial/by-id/..., udev
        # aliases); match against the resolved device node too.
        return os.path.realpath(port) in available

    def _connect_connector(self, connector: str, profile: str) -> bool:
        """Auto-connect via the OctoPrint 2.0 connector framework (moonraker,
        bambu, ...). Parameters come from the stored preferred connection so
        they match what the user configured.

        Returns True if a connect() was attempted (or a persistent error
        warrants backoff), False if we are simply waiting.
        """
        if ConnectedPrinter is None:
            self._warn_once(
                "no_framework",
                f"Connector '{connector}' requires the OctoPrint 2.0 "
                "connector framework, which is not available on this "
                "OctoPrint version. Skipping.",
            )
            return True  # persistent: back off

        connector_cls = ConnectedPrinter.find(connector)
        if connector_cls is None:
            self._warn_once(
                f"no_connector:{connector}",
                f"No connector registered for '{connector}'. Is the "
                f"corresponding connector plugin installed?",
            )
            return True  # persistent: back off

        parameters = self._get_preferred_parameters()

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
        if not self._host_reachable(connector, parameters):
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

    def _host_reachable(self, connector: str, parameters: dict) -> bool:
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

    # ------------------------------------------------------------------ #
    # Detected connection (settings display / simple API)
    # ------------------------------------------------------------------ #

    def _detected_connection(self) -> dict[str, str]:
        """Describe the connection to reconnect, for the settings display:
        label, target (serial port or host:port) and an optional warning."""
        connector = self._get_preferred_connector()
        label = CONNECTOR_LABELS.get(connector, connector)

        if self._is_serial_connector(connector):
            target = self.serial_port or ""
            warning = "" if target else (
                "No serial port detected yet; set one in OctoPrint's "
                "connection dialog or configure a forced port below."
            )
            return {"label": label, "target": target, "warning": warning}

        parameters = self._get_preferred_parameters()
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

    def on_api_get(self, request):
        """Serve the detected connection to the settings dialog, which fetches
        it every time it is shown so the display never goes stale."""
        return flask.jsonify(self._detected_connection())

    def is_api_protected(self):  # type: ignore[override]
        # The response contains the printer's host/port; logged-in users only.
        return True

    # ------------------------------------------------------------------ #
    # Settings / templates / assets
    # ------------------------------------------------------------------ #

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
        # custom_bindings=True: the template is bound by this plugin's own
        # view model (see autoconnectplus.js), which exposes the settings view
        # model as `settings` plus the live detected-connection observables.
        return [dict(type="settings", custom_bindings=True)]

    def is_template_autoescaped(self):  # type: ignore[override]
        # All expressions are plain text; nothing injects HTML, so autoescaping
        # is safe and silences OctoPrint's autoescape warning.
        return True

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

    def on_settings_save(self, data) -> dict[Any, Any]:
        enabled = self._is_enabled()
        interval = self._get_interval()

        result = octoprint.plugin.SettingsPlugin.on_settings_save(self, data)

        new_enabled = self._is_enabled()
        new_interval = self._get_interval()

        if enabled != new_enabled:
            if new_enabled:
                self._logger.info("AutoConnect enabled, starting timer")
                if self._printer.is_closed_or_error():
                    self._start_timer()
            else:
                self._logger.info("AutoConnect disabled, stopping timer")
                self._stop_timer()
        elif new_enabled and interval != new_interval:
            self._logger.info(f"Retry interval changed to {new_interval}")
            self._stop_timer()
            if self._printer.is_closed_or_error():
                self._start_timer()

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
