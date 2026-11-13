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
2. Open the **Valetudo panel** (the only toolbar button, or *Plugins > Valetudo*).
   The menu and the toolbar stay minimal: everything happens in the panel.

The panel, from top to bottom:

```
Eureka J15 Pro Ultra · Connected        [Docked]
████████████████████ 99 %
⚠ error, only when there is one
┌ Clean ─────────────────────────────────────┐
│ ☐ Kitchen  ☐ Hallway  …   (robot's rooms)  │
│ [Clean everything] [Clean ticked rooms (2)]│
│ [Clean a zone…]    [Go to…]                │
└────────────────────────────────────────────┘
┌ Robot ─────────────────────────────────────┐
│ [Pause]   [Dock]   [Locate]                │
└────────────────────────────────────────────┘
┌ Dock station ── (only if supported) ───────┐
│ [Empty bin] [Wash mops] [Dry mops]         │
└────────────────────────────────────────────┘
[Show on map]     map + live tracking + robot kept in view
Result of the last command
Robot · Camera · Maintenance · Advanced   (tabs)
```

- Every button does one thing; its tooltip says what. *Clean a zone…* and *Go to…*
  stay pressed until you have drawn the zone or clicked the point on the map.
- Rooms are chosen only in the list; the button says how many are ticked.
- **Show on map** loads the map, refreshes the robot live and keeps it in view
  (recentring only when it nears the edge). Click again to stop.
- **Tabs**: Robot (state details, dock station states, fan speed and water usage,
  attachments, position), Camera (live video, plays only while this tab is shown;
  camera light), Maintenance (consumables, statistics), Advanced (tools: no-go
  zones and walls, watch boot, reboot; Wi-Fi, events, boot log, features).
- **Go to…** on robots without Valetudo's go-to (e.g. Eureka J15 Pro Ultra): a 60 cm
  zone clean at that spot, then a pause as soon as the robot is within 50 cm (it
  cleans on its way; a first-time confirmation says so).

The panel refreshes itself while visible (every 5 s while the robot works, every
30 s otherwise), even when the robot is docked. Sections fold with their arrow
(remembered).

**Camera**: robots that expose a raw H.264 stream over TCP (default
`tcp://<robot>:8555`). Needs **ffmpeg** (found in the `PATH`, or set its path in
the settings). The stream reconnects by itself if it drops, and **Save snapshot**
saves the current image as PNG.

**No-go zones and virtual walls**: *Edit no-go zones and walls* loads the
robot's restrictions into two editable layers ("no-go zones" polygons, with a
No-go / No-mop type, and "virtual walls" lines), on top of the map. Edit them
with the usual QGIS tools (add, move vertices, snapping, undo); a bar at the top
of the map offers *Send to robot…* (after a confirmation, they replace the
robot's restrictions) and *Discard changes*. Valetudo needs 4 corners per zone and 2 points per wall: other
shapes are converted (bounding rectangle, line ends) and the confirmation says
so.

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

**Boot log**: after *Reboot robot (SSH)…*, or with *Watch robot boot* (e.g.
after using the power button), the plugin follows the robot's startup and
times each stage: offline, network up (SSH port answers), Valetudo answers
(version), robot firmware ready (state, battery), camera stream available, then
the total boot time and the end of Valetudo's own log. Lines go to the
*Valetudo* tab of the QGIS *Log Messages* panel and to the *Boot log* section of
the status panel (Info tab).

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
