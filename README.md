# dotfiles
My dotfiles. 

## Yabai

Yabai is configured as a floating window router: it sends applications and
named windows to their assigned macOS Spaces without enabling automatic
tiling. The shared routing rules and per-laptop Space mappings live in
`.yabairc`.

### Remembering window geometry across displays

The helper under `.config/yabai/scripts/` tracks normal, resizable windows:

| App | Width | Height and vertical position | Horizontal position |
| --- | --- | --- | --- |
| iTerm2, ChatGPT, Google Chrome | Fixed screen points | Percentage of usable screen | Fraction of available space around the window |

Horizontal placement uses `(window left - screen left) / (screen width -
window width)`: 0% is left-aligned, 50% is centered, and 100% is right-aligned.
This keeps fixed-width windows aligned when changing screens, including moving
from a smaller screen to a larger one. It does not preserve a fixed pixel margin.
Manual resizing updates placement against the new available space. Full-width
windows retain a previously remembered alignment; when first learned at full
width, they default to centered because their alignment is ambiguous.
Legacy records are migrated once from their last saved placement without
resetting widths or height preferences.

Resize or move a window normally to update its remembered layout. When it
changes displays (or the display resolution/arrangement changes), the helper
restores that layout after you release the mouse. Menu bars, notches and visible
Docks are excluded. Oversized frames are constrained to fit; application
minimum sizes and rounding can limit the result. Preferred proportions remain
saved so those constraints do not permanently shrink the remembered layout.
Monitor changes get repeated settling scans; temporary yabai request timeouts
are retried with longer deadlines without replacing the saved proportions.

Each live window has its own record; no app-wide defaults or title matching are
used. Newly opened windows start with their app's own geometry. Dialogs,
popovers, minimized/hidden and fullscreen windows are skipped. A Chrome window
without an accessibility reference is learned when it becomes accessible.
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
     -> migrate old saved horizontal positions
     -> learn current layouts for newly tracked windows
        (existing records can restore if their display has changed)
     -> register labelled window/display signals, replacing old registrations
  -> .yabairc loads the laptop profile and installs Space-routing rules
```

`screen-info.swift` reads macOS's usable screen bounds and whether the left mouse
button is held. Python matches those bounds to yabai's display inventory.
`window-memory.py` contains the tracked apps (`MODES` and `APP_FILTER`), layout
calculations, state handling and event handlers.

**Events**

| Trigger | Handler | Purpose |
| --- | --- | --- |
| `window_created` | `process_window(id)` | Learn a new window's current layout |
| `window_moved`, `window_resized` | `process_window(id)` | Remember adjustments, or restore after a display change |
| `window_focused`, `window_deminimized` | `process_window(id)` | Catch up when a window becomes accessible again |
| `display_added`, `display_removed` | `display_change()` -> `refresh()` | Rescan tracked windows after plugging/unplugging a monitor |
| `display_moved`, `display_resized` | `display_change()` -> `refresh()` | Rescan after display arrangement/resolution changes |

Window signals are filtered to iTerm2, ChatGPT and Google Chrome. Display signals
rescan all eligible windows from those apps. `display_change()` makes three
passes, waiting 0.5, 1 and 2 seconds before successive passes so late macOS window
migrations get another chance. It does not force windows back to a particular
monitor when one is reconnected.

**Per-window decision**

```text
event -> coalesce duplicate events with a per-window lock
  -> wait for a stable frame and mouse release (except startup seeding)
  -> skip excluded, inaccessible, hidden, minimized or fullscreen windows
  -> read the current display UUID + usable bounds and saved window record
     |
     +-- no record / different app or process -> learn current layout
     +-- display UUID or usable bounds changed -> restore saved layout
     +-- same display, frame changed -> remember the latest adjustments
     +-- nothing changed -> do nothing (also avoids reacting to our own resize)
  -> save state atomically under a shared state-file lock
```

For a restore, `target_frame()` calculates fixed width, proportional height and
vertical position, and horizontal alignment within the remaining screen space.
`restore()` moves onto the destination screen first, resizes, checks the app's
actual size, then adjusts the final position to fit. The preferred layout stays
saved separately from the resulting frame, so rounding and size constraints do
not become new preferences.

Records in `~/Library/Caches/yabai-window-memory/windows.json` are keyed by
window ID and checked against app/process identity. Display rescans prune
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
