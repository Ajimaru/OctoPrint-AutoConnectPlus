# OctoPrint-AutoConnectPlus

[![GitHub release](https://img.shields.io/github/v/release/ajimaru/OctoPrint-AutoConnectPlus?include_prereleases&sort=semver)](https://github.com/ajimaru/OctoPrint-AutoConnectPlus/releases)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)

Automatically (re)connect your printer in OctoPrint — not only over **serial**, but
also through the OctoPrint 2.0 **connector framework** for **Moonraker (Klipper)**,
**Bambu** and any other registered connector.

AutoConnectPlus is a fork of
[OctoPrint-PortRetryPlus](https://github.com/hprombex/OctoPrint-PortRetryPlus) that
keeps its proven retry/timer logic and extends it to the modern connector API.

## How it works

OctoPrint can auto-connect **once** on startup, but it does **not** retry if that
attempt fails, and it does **not** reconnect after a disconnect. On **OctoPrint 2.0+**
this applies to your *preferred* connection (the connector and its parameters); on
**OctoPrint 1.x** it applies to the classic serial connection.

AutoConnectPlus fills exactly that gap on both — keeping a printer connected by
retrying until it succeeds, and reconnecting automatically after any disconnect:

- A `RepeatedTimer` runs while the printer is disconnected and retries on a
  configurable interval.
- It reuses **OctoPrint's own preferred connection** — connector and parameters are
  read from `printerConnection.preferred`, so whatever you set up in OctoPrint's
  connection dialog (serial, Moonraker, Bambu, ...) is what gets reconnected. **There
  is nothing to configure twice.**
- For connector types it checks `connection_preconditions_met` and probes the host's
  TCP port before each attempt, so an offline printer is skipped quietly (the timer
  just keeps waiting) instead of flooding the log.
- When a connect is actually attempted but keeps failing, the retry rate backs off
  progressively, so a misconfigured connection doesn't hammer the log every interval.
- For serial it keeps the original PortRetryPlus behaviour, including the optional
  forced port when OctoPrint's serial port is unset or `AUTO`.
- Event-driven: the retry timer starts on `Disconnected` and stops on `Connected`.

OctoPrint stores a single preferred connection, so there is never any ambiguity. If
it is missing or incomplete (or the matching connector plugin is not installed), the
plugin simply keeps waiting and logs the reason once instead of every interval.

## Requirements

- **Serial** mode works on any reasonably recent OctoPrint (1.x included), where it
  behaves like classic PortRetryPlus.
- **Moonraker / Bambu** (and other connectors) require **OctoPrint 2.0+** (the
  connector framework) and the matching connector plugin installed:
  - [OctoPrint-MoonrakerConnector](https://github.com/OctoPrint/OctoPrint-MoonrakerConnector)
  - [OctoPrint-BambuConnector](https://github.com/OctoPrint/OctoPrint-BambuConnector)
    (which provides the `bpm` / bambu-printer-manager dependency — AutoConnectPlus does
    **not** install it itself).

## Installation

Install via the OctoPrint Plugin Manager using a URL.

**Latest release** (recommended) — always points at the newest release, which may be a
prerelease (e.g. an `-rc`) until the first stable version is out:

    https://github.com/ajimaru/OctoPrint-AutoConnectPlus/releases/download/latest/OctoPrint-AutoConnectPlus-latest.zip

## Configuration

First set up and connect your printer once via OctoPrint's normal **connection
dialog** (serial / Moonraker / Bambu / ...). AutoConnectPlus will then reconnect using
exactly that connection.

Open **Settings → AutoConnectPlus**. It shows the **detected connection** (type and
target, read-only) and these options:

- **Enable automatic (re)connect** — master switch, on by default. When off, the plugin
  stays idle and never reconnects.
- **Retry interval (seconds)** — how often to attempt a reconnect while disconnected.
- **Forced serial port** — *serial only, optional*. Used only when OctoPrint's serial
  port is unset or `AUTO`.

The printer profile used is OctoPrint's default profile.

## Credits

- Original [OctoPrint-PortRetryPlus](https://github.com/hprombex/OctoPrint-PortRetryPlus)
  by **hprombex**.
- Earlier work and inspiration credited to **vehystrix**.

## License

Licensed under the **GNU Affero General Public License v3 or later
(AGPL-3.0-or-later)**, matching the original project. See [LICENSE](LICENSE).

> [!NOTE]
> **About this project.** I built this for my own printer setup with AI, and if
> it helps others, even better. I have tested it to the best of my knowledge and
> ability. Disclosed here per the OctoPrint plugin guidelines.
> Issues and PRs are welcome.
