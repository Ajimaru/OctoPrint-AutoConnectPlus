"""Unit tests for the AutoConnectPlus plugin.

The plugin object is exercised directly with a fake settings store and mocked
printer/logger, so the tests run against both OctoPrint 1.x and 2.x: nothing
here requires a running server or a real connector, and the connector
framework (ConnectedPrinter) is monkeypatched at the module level.
"""

# Tests poke at plugin internals and use pytest fixtures by design.
# pylint: disable=protected-access,redefined-outer-name,unused-argument

from unittest import mock

import octoprint.plugin
import pytest

import octoprint_autoconnectplus
from octoprint_autoconnectplus import (
    CONNECTOR_DEFAULT_PORTS,
    MAX_BACKOFF_TICKS,
    AutoConnectPlusPlugin,
)


class FakeSettings:
    """Minimal stand-in for OctoPrint's PluginSettings."""

    def __init__(self):
        self.plugin = {"enabled": True, "interval": 5.0, "forced_port": ""}
        self.globals = {}

    def get(self, path, **kwargs):
        return self.plugin.get(path[-1])

    def get_boolean(self, path, **kwargs):
        return bool(self.plugin.get(path[-1]))

    def get_float(self, path, **kwargs):
        value = float(self.plugin[path[-1]])
        minimum = kwargs.get("min")
        if minimum is not None and value < minimum:
            return minimum
        return value

    def global_get(self, path, **kwargs):
        node = self.globals
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
        return node


class FakeConnector:
    """Stand-in for a registered ConnectedPrinter subclass."""

    preconditions = True
    raise_on_preconditions = False

    @classmethod
    def connection_preconditions_met(cls, params):
        if cls.raise_on_preconditions:
            raise RuntimeError("boom")
        return cls.preconditions


class FakeConnectedPrinter:
    """Stand-in for the ConnectedPrinter registry."""

    registry = {}

    @classmethod
    def find(cls, connector):
        return cls.registry.get(connector)


@pytest.fixture
def plugin():
    p = AutoConnectPlusPlugin()
    p._settings = FakeSettings()
    p._printer = mock.Mock()
    p._printer.is_closed_or_error.return_value = True
    p._printer_profile_manager = mock.Mock()
    p._printer_profile_manager.get_default.return_value = {"id": "_default"}
    p._logger = mock.Mock()
    p._plugin_name = "AutoConnectPlus"
    p._plugin_version = "0.0.0"
    return p


@pytest.fixture
def connector_framework(monkeypatch):
    """Install the fake connector registry as the module's framework."""
    FakeConnectedPrinter.registry = {}
    FakeConnector.preconditions = True
    FakeConnector.raise_on_preconditions = False
    monkeypatch.setattr(
        octoprint_autoconnectplus, "ConnectedPrinter", FakeConnectedPrinter
    )
    return FakeConnectedPrinter


def set_preferred(plugin, connector, parameters=None):
    plugin._settings.globals["printerConnection"] = {
        "preferred": {"connector": connector, "parameters": parameters or {}}
    }


# --------------------------------------------------------------------- #
# Serial port resolution
# --------------------------------------------------------------------- #


def test_serial_port_prefers_global_setting(plugin):
    plugin._settings.globals["serial"] = {"port": "/dev/ttyUSB0"}
    assert plugin.serial_port == "/dev/ttyUSB0"


def test_serial_port_falls_back_to_forced_port(plugin):
    plugin._settings.globals["serial"] = {"port": "AUTO"}
    plugin._settings.plugin["forced_port"] = "/dev/ttyACM1"
    assert plugin.serial_port == "/dev/ttyACM1"


def test_serial_port_unresolved_is_none(plugin):
    assert plugin.serial_port is None


def test_serial_port_not_cached(plugin):
    plugin._settings.globals["serial"] = {"port": "/dev/ttyUSB0"}
    assert plugin.serial_port == "/dev/ttyUSB0"
    plugin._settings.globals["serial"] = {"port": "/dev/ttyUSB1"}
    assert plugin.serial_port == "/dev/ttyUSB1"


# --------------------------------------------------------------------- #
# Backoff / warn-once
# --------------------------------------------------------------------- #


def test_register_failure_grows_and_caps(plugin):
    for expected in range(1, MAX_BACKOFF_TICKS + 5):
        plugin._register_failure()
        assert plugin._skip_ticks == min(expected, MAX_BACKOFF_TICKS)


def test_reset_failures_clears_state(plugin):
    plugin._register_failure()
    plugin._warned_keys.add("some_key")
    plugin._reset_failures()
    assert plugin._failures == 0
    assert plugin._skip_ticks == 0
    assert not plugin._warned_keys


def test_warn_once_logs_error_then_debug(plugin):
    plugin._warn_once("key", "message")
    plugin._warn_once("key", "message")
    plugin._logger.error.assert_called_once_with("message")
    plugin._logger.debug.assert_called_once_with("message")


# --------------------------------------------------------------------- #
# Timer condition
# --------------------------------------------------------------------- #


def test_timer_condition_false_while_printer_active(plugin):
    plugin._printer.is_closed_or_error.return_value = False
    assert plugin._timer_condition() is False


def test_timer_condition_counts_down_backoff(plugin):
    plugin._settings.globals["serial"] = {"port": "/dev/ttyUSB0"}
    plugin._skip_ticks = 2
    assert plugin._timer_condition() is False
    assert plugin._timer_condition() is False
    assert plugin._timer_condition() is True


def test_timer_condition_serial_requires_port(plugin):
    assert plugin._timer_condition() is False
    plugin._settings.globals["serial"] = {"port": "/dev/ttyUSB0"}
    assert plugin._timer_condition() is True


def test_timer_condition_connector_always_retries(plugin):
    set_preferred(plugin, "moonraker", {"host": "printer.local"})
    assert plugin._timer_condition() is True


# --------------------------------------------------------------------- #
# Serial connecting
# --------------------------------------------------------------------- #


def comports_returning(*devices):
    """Mock for list_ports.comports() listing the given device nodes."""
    return mock.Mock(return_value=[mock.Mock(device=d) for d in devices])


def test_connect_serial_waits_without_port(plugin):
    assert plugin._connect_serial("_default") is False
    plugin._printer.connect.assert_not_called()


def test_connect_serial_connects_when_port_present(plugin):
    plugin._settings.globals["serial"] = {"port": "/dev/ttyUSB0"}
    with mock.patch(
        "serial.tools.list_ports.comports",
        new=comports_returning("/dev/ttyACM0", "/dev/ttyUSB0"),
    ):
        assert plugin._connect_serial("_default") is True
    plugin._printer.connect.assert_called_once_with(
        port="/dev/ttyUSB0", profile="_default"
    )


def test_connect_serial_waits_while_port_absent(plugin):
    plugin._settings.globals["serial"] = {"port": "/dev/ttyUSB0"}
    with mock.patch(
        "serial.tools.list_ports.comports", new=comports_returning()
    ):
        assert plugin._connect_serial("_default") is False
    plugin._printer.connect.assert_not_called()


def test_serial_port_present_resolves_symlinks(plugin, tmp_path):
    # forced ports are often stable symlinks (/dev/serial/by-id/..., udev
    # aliases) that comports() never lists verbatim
    link = tmp_path / "printer"
    link.symlink_to("/dev/ttyUSB0")
    with mock.patch(
        "serial.tools.list_ports.comports",
        new=comports_returning("/dev/ttyUSB0"),
    ):
        assert plugin._serial_port_present(str(link)) is True


def test_serial_port_present_optimistic_on_enumeration_error(plugin):
    with mock.patch(
        "serial.tools.list_ports.comports", side_effect=OSError("boom")
    ):
        assert plugin._serial_port_present("/dev/ttyUSB0") is True


# --------------------------------------------------------------------- #
# Connector connecting
# --------------------------------------------------------------------- #


def test_connector_backs_off_without_framework(plugin, monkeypatch):
    monkeypatch.setattr(octoprint_autoconnectplus, "ConnectedPrinter", None)
    assert plugin._connect_connector("moonraker", "_default") is True
    plugin._printer.connect.assert_not_called()
    plugin._logger.error.assert_called_once()


def test_connector_backs_off_when_not_registered(plugin, connector_framework):
    assert plugin._connect_connector("moonraker", "_default") is True
    plugin._printer.connect.assert_not_called()
    plugin._logger.error.assert_called_once()


def test_connector_waits_when_preconditions_unmet(plugin, connector_framework):
    connector_framework.registry["moonraker"] = FakeConnector
    FakeConnector.preconditions = False
    set_preferred(plugin, "moonraker", {"host": "printer.local"})
    assert plugin._connect_connector("moonraker", "_default") is False
    plugin._printer.connect.assert_not_called()


def test_connector_connects_when_reachable(plugin, connector_framework):
    connector_framework.registry["moonraker"] = FakeConnector
    parameters = {"host": "printer.local", "port": 7125}
    set_preferred(plugin, "moonraker", parameters)
    with mock.patch.object(plugin, "_host_reachable", return_value=True):
        assert plugin._connect_connector("moonraker", "_default") is True
    plugin._printer.connect.assert_called_once_with(
        connector="moonraker", parameters=parameters, profile="_default"
    )


def test_connector_connects_despite_precondition_error(
    plugin, connector_framework
):
    connector_framework.registry["moonraker"] = FakeConnector
    FakeConnector.raise_on_preconditions = True
    set_preferred(plugin, "moonraker", {"host": "printer.local"})
    with mock.patch.object(plugin, "_host_reachable", return_value=True):
        assert plugin._connect_connector("moonraker", "_default") is True
    plugin._printer.connect.assert_called_once()


def test_connector_waits_while_host_unreachable(plugin, connector_framework):
    connector_framework.registry["moonraker"] = FakeConnector
    set_preferred(plugin, "moonraker", {"host": "printer.local"})
    with mock.patch.object(plugin, "_host_reachable", return_value=False):
        assert plugin._connect_connector("moonraker", "_default") is False
    plugin._printer.connect.assert_not_called()


# --------------------------------------------------------------------- #
# Host reachability probe
# --------------------------------------------------------------------- #


def test_host_reachable_true_on_open_port(plugin):
    with mock.patch("socket.create_connection") as create:
        assert plugin._host_reachable("moonraker", {"host": "h"}) is True
    create.assert_called_once()
    # no explicit port: the connector default must be probed
    default_port = CONNECTOR_DEFAULT_PORTS["moonraker"]
    assert create.call_args[0][0] == ("h", default_port)


def test_host_reachable_false_on_refused(plugin):
    with mock.patch("socket.create_connection", side_effect=OSError):
        assert plugin._host_reachable("moonraker", {"host": "h"}) is False


def test_host_reachable_optimistic_without_host_or_port(plugin):
    with mock.patch("socket.create_connection") as create:
        assert plugin._host_reachable("moonraker", {}) is True
        assert plugin._host_reachable("unknown", {"host": "h"}) is True
        assert (
            plugin._host_reachable("unknown", {"host": "h", "port": "nan"})
            is True
        )
    create.assert_not_called()


# --------------------------------------------------------------------- #
# do_auto_connect
# --------------------------------------------------------------------- #


def test_do_auto_connect_noop_while_printer_active(plugin):
    plugin._printer.is_closed_or_error.return_value = False
    plugin.do_auto_connect()
    plugin._printer.connect.assert_not_called()
    assert plugin._skip_ticks == 0


def test_do_auto_connect_backs_off_after_attempt(plugin):
    with mock.patch.object(plugin, "_connect_serial", return_value=True):
        plugin.do_auto_connect()
    assert plugin._skip_ticks == 1


def test_do_auto_connect_backs_off_on_exception(plugin):
    with mock.patch.object(
        plugin, "_connect_serial", side_effect=RuntimeError("boom")
    ):
        plugin.do_auto_connect()
    assert plugin._skip_ticks == 1
    plugin._logger.error.assert_called_once()


# --------------------------------------------------------------------- #
# Detected connection (settings display / simple API)
# --------------------------------------------------------------------- #


def test_detected_connection_serial_with_port(plugin):
    plugin._settings.globals["serial"] = {"port": "/dev/ttyUSB0"}
    detected = plugin._detected_connection()
    assert detected == {
        "label": "Serial",
        "target": "/dev/ttyUSB0",
        "warning": "",
    }


def test_detected_connection_serial_without_port_warns(plugin):
    detected = plugin._detected_connection()
    assert detected["label"] == "Serial"
    assert detected["target"] == ""
    assert "No serial port" in detected["warning"]


def test_detected_connection_connector_with_default_port(
    plugin, connector_framework
):
    connector_framework.registry["moonraker"] = FakeConnector
    set_preferred(plugin, "moonraker", {"host": "printer.local"})
    detected = plugin._detected_connection()
    assert detected["label"] == "Moonraker (Klipper)"
    assert detected["target"] == "printer.local:7125"
    assert detected["warning"] == ""


def test_detected_connection_warns_without_stored_host(
    plugin, connector_framework
):
    set_preferred(plugin, "moonraker", {})
    detected = plugin._detected_connection()
    assert "No preferred connection stored" in detected["warning"]


def test_detected_connection_warns_when_connector_missing(
    plugin, connector_framework
):
    set_preferred(plugin, "bambu", {"host": "printer.local"})
    detected = plugin._detected_connection()
    assert "not installed" in detected["warning"]


# --------------------------------------------------------------------- #
# Timer lifecycle
# --------------------------------------------------------------------- #


def test_start_timer_respects_enabled_flag(plugin):
    plugin._settings.plugin["enabled"] = False
    with mock.patch("octoprint_autoconnectplus.RepeatedTimer") as timer_cls:
        plugin._start_timer()
    timer_cls.assert_not_called()
    assert plugin._timer is None


def test_start_timer_never_rearms_running_timer(plugin):
    with mock.patch("octoprint_autoconnectplus.RepeatedTimer") as timer_cls:
        plugin._start_timer()
        plugin._start_timer()
    timer_cls.assert_called_once()
    timer_cls.return_value.start.assert_called_once()


def test_on_event_connected_stops_and_resets(plugin):
    timer = mock.Mock()
    plugin._timer = timer
    plugin._skip_ticks = 3
    plugin.on_event("Connected", {})
    timer.cancel.assert_called_once()
    assert plugin._timer is None
    assert plugin._skip_ticks == 0


def test_on_event_disconnected_starts_timer(plugin):
    with mock.patch("octoprint_autoconnectplus.RepeatedTimer") as timer_cls:
        plugin.on_event("Disconnected", {})
    timer_cls.return_value.start.assert_called_once()


def test_on_shutdown_stops_timer(plugin):
    timer = mock.Mock()
    plugin._timer = timer
    plugin.on_shutdown()
    timer.cancel.assert_called_once()
    assert plugin._timer is None


# --------------------------------------------------------------------- #
# Settings save
# --------------------------------------------------------------------- #


@pytest.fixture
def save_settings(plugin):
    """Patch the base on_settings_save to apply plain dict updates."""

    def do_save(changes):
        with mock.patch.object(
            octoprint.plugin.SettingsPlugin,
            "on_settings_save",
            side_effect=lambda _self, data: plugin._settings.plugin.update(
                data
            ),
            autospec=True,
        ):
            return plugin.on_settings_save(changes)

    return do_save


def test_settings_save_disable_stops_timer(plugin, save_settings):
    timer = mock.Mock()
    plugin._timer = timer
    save_settings({"enabled": False})
    timer.cancel.assert_called_once()
    assert plugin._timer is None


def test_settings_save_enable_starts_timer_when_disconnected(
    plugin, save_settings
):
    plugin._settings.plugin["enabled"] = False
    with mock.patch("octoprint_autoconnectplus.RepeatedTimer") as timer_cls:
        save_settings({"enabled": True})
    timer_cls.return_value.start.assert_called_once()


def test_settings_save_interval_change_restarts_timer(plugin, save_settings):
    old_timer = mock.Mock()
    plugin._timer = old_timer
    with mock.patch("octoprint_autoconnectplus.RepeatedTimer") as timer_cls:
        save_settings({"interval": 10.0})
    old_timer.cancel.assert_called_once()
    timer_cls.return_value.start.assert_called_once()


def test_settings_save_interval_change_no_restart_while_connected(
    plugin, save_settings
):
    plugin._printer.is_closed_or_error.return_value = False
    old_timer = mock.Mock()
    plugin._timer = old_timer
    with mock.patch("octoprint_autoconnectplus.RepeatedTimer") as timer_cls:
        save_settings({"interval": 10.0})
    old_timer.cancel.assert_called_once()
    timer_cls.assert_not_called()
