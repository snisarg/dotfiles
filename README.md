# dotfiles
My dotfiles. 

## Yabai

Yabai is configured as a floating window router: it sends applications and
named windows to their assigned macOS Spaces without enabling automatic
tiling. The shared routing rules and per-laptop Space mappings live in
`.yabairc`.

### Remembering window geometry across displays

The helper under `.config/yabai/scripts/` uses the same defaults for **all
eligible apps**, including iTerm2, ChatGPT, Chrome, PyCharm and its remote client.
There are no app-specific width rules.

| Property | Default |
| --- | --- |
| Width | Remember fixed screen points; cap to fit smaller screens without forgetting the preference |
| Horizontal position | Classify left / center / right; scale the selected edge gap, or center exactly |
| Height and vertical position | Preserve proportional top and bottom gaps for every window |
| Tracking | Enabled for eligible windows |

#### Horizontal alignment and fixed widths

Measure gaps within the usable screen area:

```text
left_gap = window_left - screen_left
right_gap = screen_right - window_right

if abs(left_gap - right_gap) <= 0.15 * usable_screen_width:
    alignment = center
elif left_gap < right_gap:
    alignment = left
else:
    alignment = right
```

The **15% threshold is inclusive** and uses the full usable screen width, not the
remaining space around the window. Center-aligned windows restore exactly in the
middle with equal gaps, not with their previous small center offset.

| Alignment | Restore on the destination screen |
| --- | --- |
| Left | `screen_left + saved_left_gap_fraction * screen_width` |
| Right | `screen_right - actual_window_width - saved_right_gap_fraction * screen_width` |
| Center | `screen_left + (screen_width - actual_window_width) / 2` |

Every window retains its preferred width. A 600-point window on a 1000-point-wide
screen remains 600 points wide on a 2000-point screen: a 5% left or right gap
grows from 50 to 100 points; a centered window's equal gaps grow from 200 to 700.
If an app imposes a minimum/maximum width, the final position uses its actual
resulting width. A width larger than the screen is capped without forgetting it.

#### Proportional vertical geometry

Every window remembers its height fraction and vertical placement. Equivalently,
its top and bottom gaps scale with usable screen height:

```text
new_top_gap = saved_top_gap_fraction * new_screen_height
new_bottom_gap = saved_bottom_gap_fraction * new_screen_height
new_height = new_screen_height - new_top_gap - new_bottom_gap
```

Zero gaps naturally stay full-height. Small gaps stay proportionally small, and
shorter windows scale too. There is **no near-full-height threshold or separate
stickiness rule**. Internally, a height fraction plus the nearer vertical edge
gap represents the same geometry without storing redundant margins. Equal
vertical gaps choose bottom; with proportional height they remain equal anyway.
If an app's size limits prevent the requested height, the nearer vertical edge
is prioritized and the preferred height fraction stays saved.

#### Configuration and exceptions

Settings near the top of `.config/yabai/scripts/window-memory.py` are:

```python
DEFAULTS = {
    "enabled": True,
    "horizontal_anchor": "auto",
    "vertical_anchor": "auto",
    "width_mode": "fixed",
}
APP_OVERRIDES = {}
HORIZONTAL_CENTER_GAP_FRACTION = 0.15
```

All apps inherit these defaults. Optional sparse app exceptions can set
`enabled=False` to opt out entirely, or `width_mode` to `relative` (proportional
width) or `None` (leave width to the app/macOS). No such exceptions are currently
configured. `fixed` means remembered screen points, not physical pixels.

An axis accepts `auto`, `off`, or an explicit reference: `left`, `center`, `right`
for `horizontal_anchor`; `top`, `center`, `bottom` for `vertical_anchor`. `off`
leaves that coordinate to the app/macOS without disabling sizing. An explicit
`center` override preserves a signed center offset; automatic horizontal center
classification instead restores exactly centered.

After following the sync-in/edit/sync-out workflow below, reload the helper to
refresh subscriptions and seed existing windows:

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
PyCharm and its remote-development client (`jetbrains_client`) follow the same
fixed-width and proportional-height policies as every other app. They remain excluded
from app-to-Space routing: no rule sends their project windows or dialogs to a
particular screen or Space. Nonstandard dialogs are skipped by the eligibility
checks above.

Move or resize a window normally to update its remembered layout. An x/width
change updates horizontal alignment; a y/height change updates vertical intent
and the proportional height. Position-only edits do not overwrite constrained preferred
sizes. There is no continuous snapping. Restoring happens only
when the display UUID or usable bounds change, after movement settles and you
release the mouse. Reconnecting a screen does not force windows back onto it.

Menu bars, notches and visible Docks are excluded from usable bounds. Requested
frames are constrained to fit, and app minimum sizes/rounding can limit results.
Native app/macOS placement limits can also override a requested coordinate.
Preferred sizes and anchors remain separate from the resulting frame: constraints
and the helper's own restores do not become new preferences. Manually filling an
axis gives equal zero edge gaps, so `auto` learns horizontal center or vertical
bottom (with full proportional height). Automatic
screen-change constraints do not reclassify the saved anchor.

Old cache formats migrate without resetting known preferences or moving unchanged
windows. Migration uses saved source geometry rather than a live constrained
frame. If an old horizontal record lacks a recoverable gap, its available-space
alignment is retained temporarily until a fitting baseline allows conversion.
Previously discarded arbitrary vertical history cannot be recovered; the saved
observation becomes the new baseline.
The one-time `horizontal-center-15-proportional-height` update reclassifies saved
horizontal preferences with the center band, preserving known fixed widths and
height fractions. Previously unmanaged sizes are seeded from saved source-screen
observations, not the destination's clamped live frame. These observations may
already reflect older app/macOS constraints; discarded historical sizes cannot
be reconstructed. Migration does not center or resize unchanged windows merely
to update the cache; the new layout applies on their next screen change.

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
     -> normalize/migrate old records into independent position and size preferences
     -> learn current layouts for newly tracked windows
        (existing records can restore if their display has changed)
     -> register labelled window/display signals, replacing old registrations
  -> .yabairc loads the laptop profile and installs Space-routing rules
```

`screen-info.swift` reads macOS's usable screen bounds and whether the left mouse
button is held. Python matches those bounds to yabai's display inventory.
`window-memory.py` contains defaults and optional app overrides, the horizontal
center threshold, shared axis math and restoration, state handling and event handlers.

**Events**

| Trigger | Handler | Purpose |
| --- | --- | --- |
| `window_created` | `process_window(id)` | Learn a new window's current layout |
| `window_moved`, `window_resized` | `process_window(id)` | Remember adjustments, or restore after a display change |
| `window_focused`, `window_deminimized` | `process_window(id)` | Catch up when a window becomes accessible again |
| `display_added`, `display_removed` | `display_change()` -> `refresh()` | Rescan tracked windows after plugging/unplugging a monitor |
| `display_moved`, `display_resized` | `display_change()` -> `refresh()` | Rescan after display arrangement/resolution changes |

Window signals cover enabled apps, not just apps with width overrides. Display
signals rescan all eligible windows. `display_change()` makes three
passes, waiting 0.5, 1 and 2 seconds before successive passes so late macOS window
migrations get another chance. Scans skip unchanged windows, avoiding repeated
settling delays for every app.

**Per-window decision**

```text
event -> coalesce duplicate events with a per-window lock
  -> wait for a stable frame and mouse release (except startup seeding)
  -> skip excluded, inaccessible, hidden, minimized or fullscreen windows
  -> read the current display UUID + usable bounds and saved window record
     |
     +-- no record / different app or process -> learn current layout
     +-- display UUID or usable bounds changed
     |    -> choose remembered fixed width and proportional height
     |    -> restore each enabled position axis with its saved anchor and offset
     +-- same display, frame changed -> learn only the affected axis/size preferences
     +-- nothing changed -> do nothing (also avoids reacting to our own resize)
  -> save state atomically under a shared state-file lock
```

`policy()` merges sparse app overrides over defaults. `capture_position()` uses
`capture_axis()` with the 15% center band only for horizontal classification;
`target_axis()` restores each saved reference. `relative_height()` records every
window's height fraction; `target_layout()` combines sizes with both anchors. One `restore_layout()` moves onto the destination
screen, refreshes untouched dimensions, resizes as needed, queries the actual
app-resulting size, then corrects enabled coordinates. Opted-out coordinates stay
as returned by the app/macOS. Absolute resize commands are safe to retry without
applying a relative size delta twice.

Records in `~/Library/Caches/yabai-window-memory/windows.json` are keyed by
window ID and checked against app/process identity. `position` holds independent
x/y anchors, offsets and the rules that produced them (or `null` per opted-out
axis). `position_model` identifies the current horizontal-center/proportional-height
algorithm so old records are upgraded once. `width_preferences` holds its mode
and value (or `null` for an explicit width opt-out); `height_fraction` holds the
proportional height for every tracked window. `last_frame` and `display` are
observations, not preferred dimensions. Changing a rule reseeds only that
preference without moving the window or resetting unrelated intent.
`state_store()` centralizes normalization, locking and atomic saves; display
rescans prune records for closed windows. Temporary yabai query/move/resize
timeouts retry with 5-, 10- and 20-second deadlines; failed restores keep the prior record rather
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
