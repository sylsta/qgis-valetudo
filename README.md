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

1. *Plugins > Valetudo > Settings…*: enter the robot address (e.g. `http://192.168.1.23`)
   and click **Test connection**.
2. Click the **Valetudo panel** button. That's the whole interface:

   ```
   Eureka J15 Pro Ultra        [Docked]  ⋯
   ████████████████████ 99 %
   ⚠ problems, only when there are some
   ☐ Kitchen  ☐ Hallway  …      (rooms, if the robot knows them)
   [ Clean everything ]         (or "Clean 2 room(s)" when rooms are ticked)
   [ Pause ]        [ Dock ]
   [ Clean a zone ] [ Go to… ]  (then draw / click on the map)
   [ Show on map ]              (robot, rooms and path, live)
   ▸ Camera
   ```

   The **⋯** menu holds the rare tools: locate (beep), empty dust bin, wash and
   dry mops, no-go zones and virtual walls, boot log, reboot over SSH, settings.
   Tools the robot does not support are not shown.

**Go to… on robots without go-to** (e.g. Eureka J15 Pro Ultra): the plugin sends a
60 cm zone clean at that spot and pauses the robot as soon as it is within 50 cm
(it cleans on its way; a first-time confirmation says so).

**Camera**: open the Camera section; the video plays only while it is open. Needs a
raw H.264 stream over TCP (default `tcp://<robot>:8555`) and **ffmpeg**.

**No-go zones and virtual walls** (⋯ menu): edit them as two QGIS layers, then
*Send to robot…* from the bar at the top of the map. Valetudo needs 4 corners per
zone and 2 points per wall; other shapes are converted and the confirmation says so.

**Notifications**: the plugin checks the dock every minute and warns when the fresh
water or detergent is empty, the waste water or dust bag is full, or a tank is
missing (can be turned off in the settings).

**Reboot over SSH** (⋯ menu): needs key-based SSH login to the robot; no password
is stored. The boot is then followed stage by stage in the *Valetudo* tab of the
QGIS *Log Messages* panel.

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
| `boot_monitor.py`            | Follows and times the robot's boot, stage by stage      |
| `ssh_reboot.py`              | Robot reboot over SSH (key-based, non-blocking)         |
| `restrictions.py`            | Editable no-go zones / virtual walls and their payload  |
| `goto_emulation.py`          | "Go here" via a tiny zone clean + pause on arrival      |
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
- `python3 scripts/check_names.py` reports undefined names, e.g. a missing import
  (also run in CI).
- `scripts/compile_translations.sh` builds the `.qm` files. They are not committed.

To add a language, copy the `.ts` file to `valetudo_qgis_<lang>.ts`, set its
`language` attribute and translate the entries.

## Roadmap

- Option to keep the robot centred during live tracking.
- Replace polling with Valetudo's SSE stream.

## License

GPL v2 or later — see [LICENSE](LICENSE).
