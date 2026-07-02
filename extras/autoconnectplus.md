---
layout: plugin

id: autoconnectplus
title: OctoPrint-AutoConnectPlus
description: Automatically reconnects the printer over serial, Moonraker or Bambu connectors on a configurable interval
author: ajimaru
license: AGPLv3

date: 2026-07-02

homepage: https://github.com/ajimaru/OctoPrint-AutoConnectPlus
source: https://github.com/ajimaru/OctoPrint-AutoConnectPlus
archive: https://github.com/ajimaru/OctoPrint-AutoConnectPlus/archive/main.zip

tags:
- autoconnect
- reconnect
- disconnect
- recovery
- serial
- moonraker
- klipper
- bambu
- connector

compatibility:
  octoprint:
  - 1.10.0

  # No OS restriction: serial (pyserial) and the TCP-based connectors work on
  # all platforms OctoPrint runs on.

  python: ">=3.9,<4"
---

OctoPrint can auto-connect **once** on startup, but it does not retry if that
attempt fails and does not reconnect after a disconnect. AutoConnectPlus fills
that gap: while the printer is disconnected it retries on a configurable
interval until the connection is back — not only over **serial**, but also
through the OctoPrint 2.0 **connector framework** for **Moonraker (Klipper)**,
**Bambu** and any other registered connector.

There is nothing to configure twice: the plugin reuses **OctoPrint's own
preferred connection** (whatever you last set up in the connection dialog).
Offline printers are detected with a quick reachability probe and skipped
quietly, and repeated failed attempts back off progressively instead of
flooding the log.

AutoConnectPlus is a fork of [OctoPrint-PortRetryPlus](https://github.com/hprombex/OctoPrint-PortRetryPlus)
by hprombex (with earlier work credited to vehystrix). The serial retry/timer
logic is carried over; the connector support is new.

## Requirements

- **Serial** mode works on any reasonably recent OctoPrint (1.x included).
- **Moonraker / Bambu** (and other connectors) require **OctoPrint 2.0+** (the
  connector framework) and the matching connector plugin installed
  (OctoPrint-MoonrakerConnector / OctoPrint-BambuConnector). Bambu's `bpm`
  dependency comes from the BambuConnector plugin; AutoConnectPlus does not
  install it itself.

## Configuration

First connect your printer once via OctoPrint's normal **connection dialog**;
AutoConnectPlus will then reconnect using exactly that connection. Under
**Settings → AutoConnectPlus** you can see the detected connection and adjust:

- **Enable automatic (re)connect** — master switch, on by default.
- **Retry interval (seconds)** — how often to retry while disconnected.
- **Forced serial port** — *serial only, optional*. Used only when OctoPrint's
  serial port is unset or `AUTO`.

The same options in `~/.octoprint/config.yaml`:

```yaml
plugins:
  autoconnectplus:
    enabled: true
    interval: 5.0
    forced_port: /dev/ttyUSB0
```
