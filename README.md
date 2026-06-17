# OctoPrint-AutoConnectPlus

Automatically (re)connect your printer in OctoPrint — not only over **serial**, but
also through the new OctoPrint 2.0 **connector framework** for **Moonraker (Klipper)**
and **Bambu** printers.

AutoConnectPlus is a fork of
[OctoPrint-PortRetryPlus](https://github.com/hprombex/OctoPrint-PortRetryPlus) that
keeps its proven retry/timer logic and extends it to the modern connector API.

## Features

- **Serial** — original PortRetryPlus behaviour: probes the serial port and reconnects
  using the legacy connection API. Works on older serial-only OctoPrint too.
- **Moonraker** — reconnects via `printer.connect(connector="moonraker", ...)` using
  host / port / API key.
- **Bambu** — reconnects via `printer.connect(connector="bambu", ...)` using
  host / serial / access code.
- Event-driven: the retry timer starts on `Disconnected` and stops on `Connected`.
- Configurable retry interval.
- Optional precondition check (e.g. host resolvable) before each connector attempt,
  to avoid noisy repeated failures.

## Requirements

- **Serial** mode works on any reasonably recent OctoPrint.
- **Moonraker / Bambu** modes require **OctoPrint 2.0+** (the connector framework) and
  the matching connector plugin installed:
  - [OctoPrint-MoonrakerConnector](https://github.com/OctoPrint/OctoPrint-MoonrakerConnector)
  - [OctoPrint-BambuConnector](https://github.com/OctoPrint/OctoPrint-BambuConnector)
    (which provides the `bpm` / bambu-printer-manager dependency — AutoConnectPlus does
    **not** install it itself).

If you select a connector type on an OctoPrint without the connector framework,
AutoConnectPlus logs a clear error and does nothing — serial mode still works.

## Installation

Install via the OctoPrint Plugin Manager using this URL:

    https://github.com/ajimaru/OctoPrint-AutoConnectPlus/archive/main.zip

or manually:

    pip install "https://github.com/ajimaru/OctoPrint-AutoConnectPlus/archive/main.zip"

## Configuration

Open **Settings → AutoConnectPlus**:

- **Connection type** — `Serial`, `Moonraker`, or `Bambu`.
- **Retry interval (seconds)** — how often to attempt a reconnect while disconnected.
- **Serial**: optional **Forced serial port** (used only when the global serial port is
  unset or `AUTO`).
- **Moonraker**: **Host**, **Port** (default `7125`), **API key** (optional).
- **Bambu**: **Host**, **Serial**, **Access code**.

The printer profile used is OctoPrint's default profile.

## How it works

A `RepeatedTimer` runs while the printer is disconnected and calls the auto-connect
routine on each tick. The routine dispatches on the configured connection type:

- *serial* → probes `serial.Serial(port, baudrate)` then `printer.connect(port=, profile=)`.
- *moonraker / bambu* → no device-node probe; builds the parameter dict, optionally
  checks `connection_preconditions_met`, then calls
  `printer.connect(connector=..., parameters=..., profile=...)`.

All attempts are wrapped in robust error handling, since connector connects are
asynchronous and raise different exception types than serial.

## Credits

- Original [OctoPrint-PortRetryPlus](https://github.com/hprombex/OctoPrint-PortRetryPlus)
  by **hprombex**.
- Earlier work and inspiration credited to **vehystrix**.

## License

Licensed under the **GNU Affero General Public License v3 (AGPLv3)**, matching the
original project. See [LICENSE](LICENSE).
