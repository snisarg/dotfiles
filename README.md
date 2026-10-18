# dotfiles
My dotfiles. 

## Yabai

Yabai is configured as a floating window router: it sends applications and
named windows to their assigned macOS Spaces without enabling automatic
tiling. The shared routing rules and per-laptop Space mappings live in
`.yabairc`.

### Remembering window geometry across displays

The helper under `.config/yabai/scripts/` has one global height policy for normal,
resizable windows. App-specific settings control **width only**, with horizontal
alignment retained alongside it:

| App | Width | Horizontal position |
| --- | --- | --- |
| iTerm2, ChatGPT, Google Chrome | Fixed screen points | Fraction of available space around the window |

The app-specific configuration is just:

```python
WIDTH_MODES = {
    "iTerm2": "fixed",
    "ChatGPT": "fixed",
    "Google Chrome": "fixed",
}
```

`fixed` remembers width in screen points; `relative` remembers width as a screen
fraction. Omit an app to leave its width/horizontal placement to macOS and the app.
There are no app-specific height or vertical-position options.

Horizontal placement uses `(window left - screen left) / (screen width -
window width)`: 0% is left-aligned, 50% is centered, and 100% is right-aligned.
This keeps fixed-width windows aligned when changing screens, including moving
from a smaller screen to a larger one. It does not preserve a fixed pixel margin.
Manual resizing updates placement against the new available space. Full-width
windows retain a previously remembered alignment; when first learned at full
width, they default to centered because their alignment is ambiguous.
Legacy records are migrated without resetting widths or alignment. Old per-app
height/y properties are removed; only qualifying near-full-height intent becomes
part of the global height policy.

Resize or move a window normally to update its remembered layout. When it
changes displays (or the display resolution/arrangement changes), the helper
restores configured widths/alignment and any active global height intent after
you release the mouse. Ordinary shorter windows keep the height and vertical
position chosen by macOS/the app, even for iTerm2, ChatGPT and Chrome. Menu bars,
notches and visible Docks are excluded. Oversized requested frames are constrained
to fit; application minimum sizes and rounding can limit the result. Preferred
widths, alignments and full-height margins remain saved separately from the
resulting frame, so those constraints do not become new preferences.
Monitor changes get repeated settling scans; temporary yabai request timeouts
are retried with longer deadlines without replacing the saved proportions.

#### Preserve near-full-height windows across apps

All eligible apps, including those in `WIDTH_MODES`, use the same full or
almost-full-height behavior across display changes. A window qualifies when it
occupies at least **95% of usable height**,
with **both** top and bottom gaps no larger than **3% of usable height**.
Full-height windows stay edge-to-edge; almost-full-height windows retain their
small proportional top and bottom margins. This is about height, not width.

This policy changes **only height and vertical position**. For apps without a
width rule it keeps the width and horizontal placement left by the app/macOS
after migration. Apps with a width rule additionally restore that width and its
alignment; there is no separate height override for them.

Manual vertical changes update qualification: making a window shorter or moving
it away from the usable edges opts it out; making it near-full-height again opts
it back in. There is no continuous snapping while you work. Non-qualifying
windows have an inactive baseline recorded too, so macOS clamping a short window
to full height during unplug is not mistaken for user intent.

Settings live near the top of `.config/yabai/scripts/window-memory.py`:

```python
FULL_HEIGHT_SETTINGS = {
    "enabled": True,
    "minimum_height_fraction": 0.95,
    "maximum_edge_gap_fraction": 0.03,
    "excluded_apps": ("PyCharm", "jetbrains_client"),
}
```

`jetbrains_client` covers PyCharm's remote-development client. These exclusions
and the global enable switch govern height for every app; a width rule does not
override them. Window signal filters are derived automatically from
these settings and `WIDTH_MODES`. After following the sync-in/edit/sync-out workflow
below, reload the helper to refresh its filters and seed existing windows:

```sh
python3 ~/.config/yabai/scripts/window-memory.py install
```

#### Window eligibility and state

Each live window has its own geometry record. The configuration matches app
names, not window titles; windows do not share an app-wide size template. Newly
opened windows start with their app's own geometry. Only movable,
resizable `AXStandardWindow` windows with an accessibility reference are
considered. Popovers, other dialog types, minimized/hidden and fullscreen windows
are skipped. A Chrome window without an accessibility reference is learned when
it becomes accessible.
PyCharm is excluded from both app-to-Space routing and geometry tracking.
Its project windows and dialogs are left alone.

Runtime records and the compiled screen helper live in
`~/Library/Caches/yabai-window-memory/`, not Git. Python 3 and the Xcode command
line tools (`xcrun swiftc`) are required; the screen helper compiles once at
startup, then only recompiles when its source changes. No extra Accessibility
permission or scripting addition is needed beyond yabai's existing access.

To forget remembered geometry, stop yabai, remove
`~/Library/Caches/yabai-window-memory/windows.json`, then start yabai again.
Tests run without moving live windows:

```sh
python3 -m unittest discover -s tests
```

#### Code flow and triggers

The geometry helper is **signal-driven**, not a continuously running poller.
Space routing in `.yabairc` is separate: it decides which Desktop an app belongs
on; the geometry helper decides its size and position on its current display.

**Startup**

```text
yabai starts -> .yabairc waits for a valid Space inventory
  -> window-memory.py install
     -> compile screen-info.swift if missing or changed
     -> migrate old records into width-only preferences + global height intent
     -> learn current layouts for newly tracked windows
        (existing records can restore if their display has changed)
     -> register labelled window/display signals, replacing old registrations
  -> .yabairc loads the laptop profile and installs Space-routing rules
```

`screen-info.swift` reads macOS's usable screen bounds and whether the left mouse
button is held. Python matches those bounds to yabai's display inventory.
`window-memory.py` contains app-specific width settings (`WIDTH_MODES`), the global
near-full-height settings (`FULL_HEIGHT_SETTINGS`), generated signal filters,
layout calculations, state handling and event handlers.

**Events**

| Trigger | Handler | Purpose |
| --- | --- | --- |
| `window_created` | `process_window(id)` | Learn a new window's current layout |
| `window_moved`, `window_resized` | `process_window(id)` | Remember adjustments, or restore after a display change |
| `window_focused`, `window_deminimized` | `process_window(id)` | Catch up when a window becomes accessible again |
| `display_added`, `display_removed` | `display_change()` -> `refresh()` | Rescan tracked windows after plugging/unplugging a monitor |
| `display_moved`, `display_resized` | `display_change()` -> `refresh()` | Rescan after display arrangement/resolution changes |

With the general policy enabled, window signals cover all apps except excluded
ones; otherwise only the explicit apps in `WIDTH_MODES` are matched. Display signals
rescan all eligible windows. `display_change()` makes three
passes, waiting 0.5, 1 and 2 seconds before successive passes so late macOS window
migrations get another chance. Scans prioritize active layouts and skip unchanged
windows, avoiding repeated settling delays for every app. This does not force
windows back to a particular monitor when one is reconnected.

**Per-window decision**

```text
event -> coalesce duplicate events with a per-window lock
  -> wait for a stable frame and mouse release (except startup seeding)
  -> skip excluded, inaccessible, hidden, minimized or fullscreen windows
  -> read the current display UUID + usable bounds and saved window record
     |
     +-- no record / different app or process -> learn current layout
     +-- display UUID or usable bounds changed
     |    -> width configured: restore width and horizontal alignment
     |    -> global full-height intent active: restore top/bottom margins
     |    -> no full-height intent: leave height/vertical position alone
     +-- same display, frame changed -> remember adjustments / update qualification
     +-- nothing changed -> do nothing (also avoids reacting to our own resize)
  -> save state atomically under a shared state-file lock
```

`target_width()` calculates the configured width and horizontal alignment.
`restore()` moves onto the destination screen first, resizes, checks the app's
actual size, then adjusts horizontal placement. Unless global full-height intent
is active, it reuses the actual current height and vertical position, not a
remembered per-app height.

For global height, `full_height_margins()` detects qualification,
`full_height_target()` scales the remembered margins, and
`restore_full_height()` moves vertically and sets the height using the actual
current width, without applying horizontal geometry preferences. Its absolute
resize commands are safe to retry without applying a relative size delta twice.

Records in `~/Library/Caches/yabai-window-memory/windows.json` are keyed by
window ID and checked against app/process identity. `width_preferences` contains
only horizontal `x`/`w` preferences (or is `null`); `full_height` contains global
top/bottom margin fractions (or is `null`). `last_frame` is an observation used
to distinguish real changes from the helper's own operations, not a per-app
height setting. Width mode changes reseed horizontal preferences without resetting
global height intent. Display rescans prune
records for closed windows. Temporary yabai query/move/resize timeouts retry with
5-, 10- and 20-second deadlines; failed restores keep the prior record rather
than learning the partially applied layout.

### First-time installation

Install Yabai and `jq`:

```sh
brew install asmvik/formulae/yabai jq
```

In System Settings:

1. Open **Privacy & Security → Accessibility** and enable
   `/opt/homebrew/bin/yabai`.
2. Open **Desktop & Dock → Mission Control** and disable
   **Automatically rearrange Spaces based on most recent use**.

Moving windows to existing Spaces works with System Integrity Protection
enabled. This configuration does not use Yabai's scripting addition.

### Selecting a laptop profile

The tracked `.yabairc` contains shared routing rules. Tracked files under
`.config/yabai/profiles/` contain each laptop's Space mappings. A machine-local
selector at `~/.config/yabai/profile` chooses the active profile and is never
copied into Git.

Deploy the config and select this laptop's profile:

```sh
./sync_out.sh --profile work yabai
```

The currently configured profile is `work`. A `personal` profile can be added
later with its own Space UUID mappings.

Start Yabai on the first setup, or restart it after a config change:

```sh
yabai --start-service
yabai --restart-service
```

Yabai's LaunchAgent starts it automatically at login. At startup, `.yabairc`
recreates logical Space labels, registers the shared rules, and applies them
to windows that macOS restored before Yabai finished loading.

### Adding another laptop

1. Create the desired Spaces and keep automatic Space rearrangement disabled.
2. Start Yabai and inspect that laptop's identifiers:

   ```sh
   yabai -m query --spaces |
     jq -r '.[] | [.index, .id, .uuid, .label] | @tsv'
   ```

3. Create `.config/yabai/profiles/personal.sh` and define
   `bind_profile_spaces`. Map the shared logical labels to that laptop's Space
   UUIDs using `label_space_by_uuid` (or `label_space_by_id` for a Space with no
   UUID). Use `work.sh` as the template.
4. Deploy it on that laptop:

   ```sh
   ./sync_out.sh --profile personal yabai
   yabai --restart-service
   ```

Space UUIDs survive ordinary restarts and Mission Control reordering, but they
change if a Space is deleted and recreated. Update only that laptop's profile
mapping when this happens.

### Sync behavior

```sh
./sync_in.sh yabai
./sync_out.sh yabai

# Explicitly target a profile instead of using the local selector:
./sync_in.sh --profile work yabai
./sync_out.sh --profile work yabai
```

- With no `--profile`, both scripts read `~/.config/yabai/profile`.
- `sync_in.sh` copies `~/.yabairc` plus the selected machine's profile into the
  matching repository paths, together with the shared geometry helper scripts.
- `sync_out.sh` deploys `.yabairc`, the selected profile and shared helper scripts,
  and updates the machine-local selector.
- `--profile <name>` or `--profile=<name>` overrides the local selector. The
  `YABAI_PROFILE` environment variable remains supported for compatibility.
