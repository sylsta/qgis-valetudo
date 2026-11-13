# Valetudo for QGIS

A learning project: display and control a [Valetudo](https://valetudo.cloud) robot
vacuum from QGIS. The plugin talks to Valetudo's local REST API (no cloud).

Available in English and French (the UI follows the QGIS interface language).

## Installation

**From a build (recommended):** each push to `main` builds a ready-to-install zip,
with compiled translations, in the *Actions* tab of this repository (artifact
`valetudo_qgis`). Unzip the downloaded artifact once to get `valetudo_qgis.zip`,
then in QGIS: *Plugins > Manage and Install Plugins > Install from ZIP*.

**For development:**

1. In QGIS: *Settings > User Profiles > Open Active Profile Folder*.
2. Go to `python/plugins/` (create it if needed).
3. Copy the `valetudo_qgis` folder of this repository there, or better, symlink it
   so that every `git pull` is picked up directly.
4. Optional, for the French UI: run `scripts/compile_translations.sh`
   (needs Qt's `lrelease`).
5. Restart QGIS and enable "Valetudo" in *Plugins > Manage and Install Plugins > Installed*.

Tip: install the **Plugin Reloader** plugin to reload the code after each change
without restarting QGIS.

## Usage

1. *Plugins > Valetudo > Settings…*: enter the robot address (e.g. `http://192.168.1.23`).
2. **Show map**: creates the "Valetudo" layer group and switches the project to "No CRS".
3. **Live tracking**: refreshes the robot position and its path.
4. Select rooms in the "Valetudo - map" layer (rooms are labelled with their
   Valetudo names), then **Clean selected rooms**.
5. **Clean zone**: drag a rectangle on the map; the robot cleans that zone.
6. **Go here**: click on the map; the robot drives to that spot.

7. **Status panel**: a dockable panel with the robot state, battery, errors, fan
   speed and water usage (editable), dock station state (fresh and waste water
   tanks, detergent, dust bag), current and total statistics, consumables,
   attachments, position, Wi-Fi, supported features and recent events. It has its
   own Start / Pause / Stop / Return to dock buttons and refreshes itself while it
   is visible (every 5 s while the robot works, every 30 s otherwise), even when
   the robot is docked. Every section folds by unticking its title (remembered);
   Wi-Fi, Features and Recent events start folded. The robot position is read
   by the panel itself, no need to show the map.

8. **Camera**: a "Camera" section at the top of the status panel shows the
   robot's live video, for robots that expose a raw H.264 stream over TCP
   (default `tcp://<robot>:8555`). It needs **ffmpeg** (found in the `PATH`, or
   set its path in the settings). Video plays while the panel is visible and the
   section is ticked; untick it to stop the video. Robots with a camera light
   get a "Camera light" switch under the video. The stream reconnects by itself
   if it drops, and **Save snapshot** saves the current image as PNG.

**Dock station notifications**: once a robot is configured, the plugin checks
the dock every minute in the background (panel open or not) and shows a
persistent warning when the fresh water tank or the detergent is empty, the
waste water tank or the dust bag is full, or a tank is missing; a short message
confirms when it is OK again. Turn it off in the settings.

**Reboot robot (SSH)…** (Valetudo menu and status panel): Valetudo deliberately
has no reboot API, so the plugin runs `ssh <user>@<robot> reboot` after a
confirmation. It needs an SSH client and key-based login to the robot
(`ssh-copy-id root@<robot>`); no password is ever stored or asked for. SSH user
and port are in the settings (`root`, 22 by default).

Actions the robot does not support are greyed out. Zone and go-to tools need the
map to be shown first, and switch back to the Pan tool after each use.

The map is displayed in the robot's own frame, in centimetres. The API reference
is the Swagger UI embedded in the robot: `http://<robot-ip>/swagger/`.

## Code layout

| File                         | Role                                                    |
|------------------------------|---------------------------------------------------------|
| `__init__.py`                | Entry point called by QGIS (`classFactory`)             |
| `metadata.txt`               | Name, version, QGIS compatibility                       |
| `plugin.py`                  | Toolbar, menu, wiring between client and layers         |
| `client.py`                  | Asynchronous HTTP calls to the Valetudo API             |
| `map_layers.py`              | Map JSON -> QGIS layers, incremental updates            |
| `status_panel.py`            | Dockable status panel with controls                     |
| `camera.py`                  | H.264 stream decoding through an ffmpeg subprocess      |
| `camera_widget.py`           | Camera video widget, embedded in the status panel       |
| `ssh_reboot.py`              | Robot reboot over SSH (key-based, non-blocking)         |
| `map_tools.py`               | Map tools: draw a cleaning zone, pick a go-to target    |
| `coords.py`                  | Frame conversion (the only place that flips the Y axis) |
| `settings_dialog.py`         | Settings (URL, authentication, refresh interval)        |
| `i18n.py`                    | `tr()` helper and translator loading                    |
| `i18n/valetudo_qgis_fr.ts`   | French translation (Qt Linguist format)                 |

## Translations

- Every user-facing string is written in English and wrapped in `tr("...")`
  (from `valetudo_qgis/i18n.py`), with a string literal as argument.
  Use named placeholders and `.format()`: `tr("Command “{command}” sent.").format(command=...)`.
- Add each new string to `valetudo_qgis/i18n/valetudo_qgis_fr.ts` (by hand or with
  Qt Linguist).
- `python3 scripts/check_translations.py` lists missing translations (also run in CI).
- `scripts/compile_translations.sh` builds the `.qm` files. They are not committed.

To add a language, copy the `.ts` file to `valetudo_qgis_<lang>.ts`, set its
`language` attribute and translate the entries.

## Roadmap

- Option to keep the robot centred during live tracking.
- Replace polling with Valetudo's SSE stream.

## License

GPL v2 or later — see [LICENSE](LICENSE).
