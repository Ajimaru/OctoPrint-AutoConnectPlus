---
layout: plugin

id: autoconnectplus
title: OctoPrint-AutoConnectPlus
description: Automatically reconnects the printer over serial, Moonraker or Bambu connectors on a configurable interval
author: ajimaru
license: AGPLv3

# today's date in format YYYY-MM-DD
date: 2026-06-17

homepage: https://github.com/ajimaru/OctoPrint-AutoConnectPlus
source: https://github.com/ajimaru/OctoPrint-AutoConnectPlus
archive: https://github.com/ajimaru/OctoPrint-AutoConnectPlus/archive/main.zip

# Set this if your plugin heavily interacts with any kind of cloud services.
#privacypolicy: your plugin's privacy policy URL

# Set this to true if your plugin uses the dependency_links setup parameter to include
# library versions not yet published on pypi. SHOULD ONLY BE USED IF THERE IS NO OTHER OPTION!
#follow_dependency_links: false

tags:
- disconnect
- recovery
- moonraker
- bambu
- connector

#screenshots:
#- url: url of a screenshot, /assets/img/...
#  alt: alt-text of a screenshot
#  caption: caption of a screenshot

#featuredimage: url of a featured image for your plugin, /assets/img/...

# You only need the following if your plugin requires specific OctoPrint versions or
# specific operating systems to function - you can safely remove the whole
# "compatibility" block if this is not the case.

compatibility:

  # List of compatible versions
  #
  # A single version number will be interpretated as a minimum version requirement,
  # e.g. "1.3.1" will show the plugin as compatible to OctoPrint versions 1.3.1 and up.
  # More sophisticated version requirements can be modelled too by using PEP440
  # compatible version specifiers.
  #
  # You can also remove the whole "octoprint" block. Removing it will default to all
  # OctoPrint versions being supported.

  octoprint:
  - 1.10.0

  # List of compatible operating systems
  #
  # Possible values:
  #
  # - windows
  # - linux
  # - macos
  # - freebsd
  #
  # There are also two OS groups defined that get expanded on usage:
  #
  # - posix: linux, macos and freebsd
  # - nix: linux and freebsd
  #
  # You can also remove the whole "os" block. Removing it will default to all
  # operating systems being supported.

  os:
  - linux
  #- windows
  #- macos
  #- freebsd

  # Compatible Python version
  python: ">=3,<4" # Python 3 only

# TODO
# If any of the below attributes apply to your project, uncomment the corresponding lines. This is MANDATORY!

#attributes:
#  - cloud  # if your plugin requires access to a cloud to function
#  - commercial  # if your plugin has a commercial aspect to it
#  - free-tier  # if your plugin has a free tier
---

When the printer is disconnected, this plugin will try to reconnect it on a
configurable interval — not only over **serial**, but also through the OctoPrint 2.0
**connector framework** for **Moonraker (Klipper)** and **Bambu** printers.

AutoConnectPlus is a fork of [OctoPrint-PortRetryPlus](https://github.com/hprombex/OctoPrint-PortRetryPlus)
by hprombex (with earlier work credited to vehystrix). The serial retry/timer logic is
carried over; the Moonraker/Bambu connector support is new.

## Requirements

- **Serial** mode works on any reasonably recent OctoPrint.
- **Moonraker / Bambu** modes require **OctoPrint 2.0+** (the connector framework) and
  the matching connector plugin installed (OctoPrint-MoonrakerConnector /
  OctoPrint-BambuConnector). Bambu's `bpm` dependency comes from the BambuConnector
  plugin; AutoConnectPlus does not install it itself.

## Configuration

Configure via **Settings → AutoConnectPlus**, or in `~/.octoprint/config.yaml`:

```
plugins:
  autoconnectplus:
    connection_type: serial   # serial | moonraker | bambu
    interval: 5
    forced_port: /dev/ttyUSB0
    moonraker:
      host: moonraker.local
      port: 7125
      apikey: ""
    bambu:
      host: 192.168.1.50
      serial: ""
      access_code: ""
```

For serial, AutoConnectPlus can work even if `Serial Connection > General > Port` is set
to `AUTO`, as long as `forced_port` is configured.
