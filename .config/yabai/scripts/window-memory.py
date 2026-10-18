#!/usr/bin/env python3
"""Remember live windows, not preset grids or ambiguous title-based templates.

Only a display/usable-area change restores geometry. Manual moves/resizes on
the same display update the saved geometry. New windows use their app's layout.
Height is governed by one global near-full-height rule. Explicit apps may also
remember width and horizontal alignment; no app has a separate height policy.
"""
import fcntl
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

WIDTH_MODES = {
    "iTerm2": "fixed",
    "ChatGPT": "fixed",
    "Google Chrome": "fixed",
}
# This vertical-only policy applies equally to apps with and without width rules.
FULL_HEIGHT_SETTINGS = {
    "enabled": True,
    "minimum_height_fraction": 0.95,
    "maximum_edge_gap_fraction": 0.03,
    "excluded_apps": ("PyCharm", "jetbrains_client"),
}
APP_FILTER = "^(" + "|".join(re.escape(app) for app in WIDTH_MODES) + ")$"
SCRIPT = Path(__file__).resolve()
CACHE = Path.home() / "Library/Caches/yabai-window-memory"
SCREEN_TOOL = CACHE / "screen-info"
STATE = CACHE / "windows.json"
YABAI = shutil.which("yabai") or "/opt/homebrew/bin/yabai"
EPSILON = 1.0  # AX frames/apps can round to whole screen points.


def run(*args):
    # Unplugging can stall yabai's AX requests for longer than five seconds.
    # Retry absolute/idempotent commands without discarding the saved layout.
    retryable = (len(args) > 2 and args[0] == YABAI
                 and args[2] in ("query", "window"))
    timeouts = (5, 10, 20) if retryable else (5,)
    for attempt, timeout in enumerate(timeouts):
        try:
            return subprocess.check_output(
                args, text=True, stderr=subprocess.PIPE, timeout=timeout)
        except subprocess.TimeoutExpired:
            if attempt == len(timeouts) - 1:
                raise
            time.sleep(0.25 * (attempt + 1))


def query(*args):
    return json.loads(run(YABAI, "-m", "query", *args))


def screen_info():
    return json.loads(run(str(SCREEN_TOOL)))


def usable_displays():
    usable = {d["id"]: d["frame"] for d in screen_info()["displays"]}
    return {
        d["index"]: {"uuid": d["uuid"], "frame": usable[d["id"]]}
        for d in query("--displays") if d["id"] in usable
    }


def full_height_enabled(app):
    return (FULL_HEIGHT_SETTINGS["enabled"]
            and app not in FULL_HEIGHT_SETTINGS["excluded_apps"])


def tracking_mode(app):
    if app in WIDTH_MODES:
        return "width"
    if full_height_enabled(app):
        return "full-height"
    return None


def width_policy_matches(record, app):
    mode = WIDTH_MODES.get(app)
    return (record.get("tracking_mode") == tracking_mode(app)
            and (mode is None or record.get("horizontal_position") ==
                 ("available-space" if mode == "fixed" else "screen")))


def eligible(window):
    return (
        tracking_mode(window.get("app")) is not None
        and window.get("subrole") == "AXStandardWindow"
        and window.get("can-move")
        and window.get("can-resize")
        and window.get("has-ax-reference")
        and not window.get("is-native-fullscreen")
        and not window.get("has-fullscreen-zoom")
        and not window.get("is-minimized")
        and not window.get("is-hidden")
    )


def window_signal_filter():
    if not FULL_HEIGHT_SETTINGS["enabled"]:
        return "app=" + APP_FILTER
    excluded = sorted(set(FULL_HEIGHT_SETTINGS["excluded_apps"]) - set(WIDTH_MODES))
    if excluded:
        return "app!=^(" + "|".join(re.escape(app) for app in excluded) + ")$"
    return None  # Listen to all apps; eligibility still filters window types.


def full_height_margins(frame, bounds):
    """Recognize near-full height only when both edges are near usable bounds."""
    height = bounds["h"]
    if height <= 0:
        return None
    top = frame["y"] - bounds["y"]
    bottom = bounds["y"] + height - frame["y"] - frame["h"]
    limit = FULL_HEIGHT_SETTINGS["maximum_edge_gap_fraction"] * height
    if (frame["h"] < FULL_HEIGHT_SETTINGS["minimum_height_fraction"] * height - EPSILON
            or frame["h"] > height + EPSILON
            or not -EPSILON <= top <= limit + EPSILON
            or not -EPSILON <= bottom <= limit + EPSILON):
        return None
    return {"top": max(0, top) / height, "bottom": max(0, bottom) / height}


def full_height_target(margins, frame, bounds):
    # Keep the post-migration width/x chosen by the app/macOS, not old geometry.
    top = round(bounds["y"] + margins["top"] * bounds["h"])
    bottom = round(bounds["y"] + bounds["h"] * (1 - margins["bottom"]))
    return dict(frame, y=top, h=max(1, bottom - top))


def close_frame(a, b):
    return all(abs(a[k] - b[k]) <= EPSILON for k in ("x", "y", "w", "h"))


def horizontal_alignment(frame, bounds):
    available = bounds["w"] - frame["w"]
    # A full-width window has no observable left/center/right preference.
    # Center is a neutral initial preference; existing preferences are kept
    # when a previously narrower window becomes constrained to full width.
    if available <= EPSILON:
        return 0.5
    return max(0, min(1, (frame["x"] - bounds["x"]) / available))


def width_preferences(frame, bounds, mode):
    return {
        "x": (horizontal_alignment(frame, bounds) if mode == "fixed"
              else (frame["x"] - bounds["x"]) / bounds["w"]),
        "w": frame["w"] / bounds["w"] if mode == "relative" else frame["w"],
    }


def target_width(saved, bounds, mode):
    # Preserve unclamped preferences: a small screen or app minimum size must
    # not permanently replace the user's larger-screen layout.
    width = saved["w"] * bounds["w"] if mode == "relative" else saved["w"]
    width = max(1, min(round(width), bounds["w"]))
    horizontal_span = bounds["w"] - width if mode == "fixed" else bounds["w"]
    return {
        "x": round(bounds["x"] + max(0, min(
            saved["x"] * horizontal_span, bounds["w"] - width))),
        "w": width,
    }


def identity(window):
    return [window["app"], window["pid"]]


def new_record(window, display):
    record = {
        "identity": identity(window),
        "display": display,
        "last_frame": window["frame"],
        "tracking_mode": tracking_mode(window["app"]),
        "full_height": (full_height_margins(window["frame"], display["frame"])
                        if full_height_enabled(window["app"]) else None),
        "width_preferences": None,
    }
    if window["app"] in WIDTH_MODES:
        record.update({
            "horizontal_position": ("available-space" if WIDTH_MODES[window["app"]] == "fixed"
                                    else "screen"),
            "width_preferences": width_preferences(
                window["frame"], display["frame"], WIDTH_MODES[window["app"]]),
        })
    return record


def migrate_record(record, mode):
    """Split old app geometry into width-only preferences and global height.

    Use saved preferences, not a live window already clamped by macOS. Discard
    per-app height/y properties; keep only qualifying near-full-height intent.
    """
    if "width_preferences" in record:
        return record
    app = record["identity"][0]
    bounds = record["display"]["frame"]
    old = record.get("proportions")
    margins = record.get("full_height")
    if old is not None:
        preferred = dict(record["last_frame"],
                         y=bounds["y"] + old["y"] * bounds["h"],
                         h=old["h"] * bounds["h"])
        margins = full_height_margins(preferred, bounds)
    migrated = {
        "identity": record["identity"], "display": record["display"],
        "last_frame": record["last_frame"],
        "tracking_mode": "width" if mode is not None else tracking_mode(app),
        "full_height": margins if full_height_enabled(app) else None,
        "width_preferences": None,
    }
    if mode is not None:
        if old is not None:
            old_mode = "relative" if record.get("horizontal_position") == "screen" else "fixed"
            if old_mode != mode:
                saved = width_preferences(record["last_frame"], bounds, mode)
            else:
                saved = {axis: old[axis] for axis in ("x", "w")}
                if mode == "fixed" and record.get("horizontal_position") != "available-space":
                    saved["x"] = horizontal_alignment(record["last_frame"], bounds)
        else:
            saved = width_preferences(record["last_frame"], bounds, mode)
        migrated.update(width_preferences=saved,
                        horizontal_position="available-space" if mode == "fixed" else "screen")
    return migrated


def updated_record(record, window, display):
    """Only manually changed axes replace saved (possibly clamped) preferences."""
    result = dict(record)
    if any(abs(window["frame"][axis] - record["last_frame"][axis]) > EPSILON
           for axis in ("y", "h")):
        result["full_height"] = (
            full_height_margins(window["frame"], display["frame"])
            if full_height_enabled(window["app"]) else None)
    if record["width_preferences"] is None:
        result.update(last_frame=window["frame"], display=display)
        return result
    saved = dict(record["width_preferences"])
    fresh = width_preferences(window["frame"], display["frame"], WIDTH_MODES[window["app"]])
    for axis in ("x", "w"):
        if abs(window["frame"][axis] - record["last_frame"][axis]) > EPSILON:
            saved[axis] = fresh[axis]
    if WIDTH_MODES[window["app"]] == "fixed":
        if display["frame"]["w"] - window["frame"]["w"] <= EPSILON:
            # Full-width/clamped frames cannot tell us a new alignment.
            saved["x"] = record["width_preferences"]["x"]
        elif abs(window["frame"]["w"] - record["last_frame"]["w"]) > EPSILON:
            # Resizing changes the available travel even if x didn't move.
            saved["x"] = fresh["x"]
    result.update(width_preferences=saved, last_frame=window["frame"], display=display)
    return result


def load_state():
    if not STATE.exists():
        return {}
    # Don't silently discard corruption and relearn a constrained layout.
    return json.loads(STATE.read_text())


def save_state(state):
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    temporary.replace(STATE)


def wait_for_settle(window_id):
    """Coalesce AX events; don't snap a window while the mouse is held."""
    previous = None
    stable = 0
    for _ in range(600):  # One minute maximum; no restore on timeout.
        window = query("--windows", "--window", str(window_id))
        if not eligible(window):
            return None
        current = (window["display"], window["frame"])
        if current == previous and not window.get("is-grabbed") \
                and not screen_info()["mouse_down"]:
            stable += 1
            if stable >= 3:
                return window
        else:
            stable = 0
        previous = current
        time.sleep(0.1)
    return None


def restore(window, target, bounds, *, vertical=True):
    window_id = str(window["id"])
    # Move first so the app adopts the destination screen's size limits.
    # Otherwise Electron can reject growth while still associated with the
    # smaller source display, even after yabai has transferred its Space.
    run(YABAI, "-m", "window", window_id, "--move",
        "abs:{x}:{y}".format(**target))
    time.sleep(0.1)
    if not vertical:
        # Width-only rules must not reapply an old height after macOS migration
        # or an application adjusting its frame during the first move.
        current = query("--windows", "--window", window_id)
        target = dict(target, h=current["frame"]["h"])
    run(YABAI, "-m", "window", window_id, "--resize",
        "abs:{w}:{h}".format(**target))
    time.sleep(0.1)
    resized = query("--windows", "--window", window_id)["frame"]
    # The app may have a larger minimum size than the requested target.
    desired_x = target["x"]
    requested_travel = bounds["w"] - target["w"]
    if WIDTH_MODES[window["app"]] == "fixed" and requested_travel > EPSILON:
        # Preserve alignment if the app rounds/constrains the requested width.
        alignment = horizontal_alignment(target, bounds)
        desired_x = bounds["x"] + alignment * max(0, bounds["w"] - resized["w"])
    x = round(max(bounds["x"], min(desired_x,
                  bounds["x"] + bounds["w"] - resized["w"])))
    y = (round(max(bounds["y"], min(target["y"],
                   bounds["y"] + bounds["h"] - resized["h"])))
         if vertical else resized["y"])
    run(YABAI, "-m", "window", window_id, "--move",
        "abs:%s:%s" % (x, y))
    time.sleep(0.1)
    return query("--windows", "--window", window_id)


def restore_full_height(window, target, bounds):
    """Change only vertical geometry, without applying horizontal preferences."""
    window_id = str(window["id"])
    run(YABAI, "-m", "window", window_id, "--move",
        "abs:%s:%s" % (window["frame"]["x"], target["y"]))
    time.sleep(0.1)
    current = query("--windows", "--window", window_id)
    # Reuse the actual current width, not a remembered width preference.
    # An absolute size is safe to retry after a timeout; a relative bottom-edge
    # delta could otherwise be applied twice if the first request did succeed.
    run(YABAI, "-m", "window", window_id, "--resize",
        "abs:%s:%s" % (current["frame"]["w"], target["h"]))
    time.sleep(0.1)
    actual = query("--windows", "--window", window_id)
    # Accommodate application minimum heights, preserving its actual x/width.
    y = round(max(bounds["y"], min(target["y"],
                  bounds["y"] + bounds["h"] - actual["frame"]["h"])))
    run(YABAI, "-m", "window", window_id, "--move",
        "abs:%s:%s" % (actual["frame"]["x"], y))
    time.sleep(0.1)
    return query("--windows", "--window", window_id)


def process_window(window_id, seed=False):
    # One worker per window; additional signals are coalesced by settling.
    with (CACHE / ("window-%s.lock" % window_id)).open("w") as window_lock:
        try:
            fcntl.flock(window_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        # Blocking intentionally: do not drop another window's event.
        with (CACHE / "state.lock").open("w") as state_lock:
            fcntl.flock(state_lock, fcntl.LOCK_EX)
            window = (query("--windows", "--window", str(window_id))
                      if seed else wait_for_settle(window_id))
            if window is None or not eligible(window):
                return
            display = usable_displays().get(window["display"])
            if display is None:
                return
            state = load_state()
            key = str(window_id)
            record = state.get(key)
            mode = tracking_mode(window["app"])
            if record is not None and record["identity"] == identity(window):
                record = migrate_record(record, WIDTH_MODES.get(window["app"]))
                if not width_policy_matches(record, window["app"]):
                    # Adding/removing a width rule doesn't reset global height intent.
                    fresh = new_record(window, display)
                    record = dict(record, tracking_mode=mode,
                                  width_preferences=fresh["width_preferences"])
                    if "horizontal_position" in fresh:
                        record["horizontal_position"] = fresh["horizontal_position"]
                    else:
                        record.pop("horizontal_position", None)
            if record is None or record["identity"] != identity(window):
                state[key] = new_record(window, display)
            elif record["display"] != display:
                target = dict(window["frame"])
                vertical = (full_height_enabled(window["app"])
                            and record["full_height"] is not None)
                if record["width_preferences"] is not None:
                    target.update(target_width(record["width_preferences"], display["frame"],
                                               WIDTH_MODES[window["app"]]))
                if vertical:
                    target = full_height_target(record["full_height"], target, display["frame"])
                if not close_frame(window["frame"], target):
                    if record["width_preferences"] is not None:
                        actual = restore(window, target, display["frame"], vertical=vertical)
                    else:
                        actual = restore_full_height(window, target, display["frame"])
                    if actual["display"] != window["display"]:
                        # Topology changed again mid-operation. Preserve the
                        # original record for the next display/focus event.
                        return
                    window = actual
                # Do not relearn full-height intent from macOS's migration
                # frame, even if a previously short window now fills the screen.
                state[key] = dict(record, display=display,
                                  last_frame=window["frame"])
            elif not close_frame(record["last_frame"], window["frame"]):
                state[key] = updated_record(record, window, display)
            else:
                if record == state[key]:
                    return  # Includes signals from our own restore.
                state[key] = record  # Persist a migration without moving it.
            save_state(state)


def refresh(seed=False):
    windows = query("--windows")
    with (CACHE / "state.lock").open("w") as state_lock:
        fcntl.flock(state_lock, fcntl.LOCK_EX)
        snapshot = load_state()
    displays = usable_displays() if snapshot else {}
    # Restore active layouts before spending time settling inactive baselines.
    windows.sort(key=lambda w: not (
        w["app"] in WIDTH_MODES
        or snapshot.get(str(w["id"]), {}).get("full_height") is not None))
    for window in windows:
        if eligible(window):
            record = snapshot.get(str(window["id"]))
            if (record is not None and record["identity"] == identity(window)
                    and width_policy_matches(record, window["app"])
                    and record["display"] == displays.get(window["display"])
                    and close_frame(record["last_frame"], window["frame"])
                    and migrate_record(record, WIDTH_MODES.get(window["app"])) == record):
                # Later monitor-settling passes usually have nothing to do.
                # Avoid another per-window settling delay for unchanged frames.
                continue
            try:
                process_window(window["id"], seed=seed)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                # Don't let one disappearing/stalled app abort the entire
                # monitor-change scan. Keep its old record for a later retry.
                print("window-memory: deferred window %s: %s" %
                      (window["id"], error), file=sys.stderr)
                continue
    # Startup inventory may still be incomplete; do not prune it at install.
    if not seed:
        with (CACHE / "state.lock").open("w") as state_lock:
            fcntl.flock(state_lock, fcntl.LOCK_EX)
            live = {str(w["id"]) for w in query("--windows")}
            state = load_state()
            save_state({key: value for key, value in state.items() if key in live})


def install():
    source = SCRIPT.with_name("screen-info.swift")
    if not SCREEN_TOOL.exists() or source.stat().st_mtime > SCREEN_TOOL.stat().st_mtime:
        subprocess.run(["xcrun", "swiftc", str(source), "-o", str(SCREEN_TOOL)],
                       check=True, timeout=120, stderr=subprocess.PIPE, text=True)
    with (CACHE / "state.lock").open("w") as state_lock:
        fcntl.flock(state_lock, fcntl.LOCK_EX)
        state = load_state()
        migrated = {
            key: migrate_record(value, WIDTH_MODES.get(value["identity"][0]))
            for key, value in state.items()
        }
        if migrated != state:
            save_state(migrated)
    refresh(seed=True)
    python = shutil.which("python3") or "/usr/bin/python3"
    command = "%s %s" % (shlex.quote(python), shlex.quote(str(SCRIPT)))
    for event in ("window_created", "window_moved", "window_resized",
                  "window_focused", "window_deminimized"):
        label = "window-memory-" + event
        subprocess.run([YABAI, "-m", "signal", "--remove", label],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        arguments = [YABAI, "-m", "signal", "--add", "label=" + label,
                     "event=" + event,
                     "action=" + command + ' window "$YABAI_WINDOW_ID"']
        app_filter = window_signal_filter()
        if app_filter is not None:
            arguments.append(app_filter)
        run(*arguments)
    for event in ("display_added", "display_removed", "display_moved", "display_resized"):
        label = "window-memory-" + event
        subprocess.run([YABAI, "-m", "signal", "--remove", label],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        run(YABAI, "-m", "signal", "--add", "label=" + label,
            "event=" + event, "action=" + command + " displays")


def display_change():
    # macOS can migrate windows after the first display notification. Recheck
    # after settling rather than relying on one early/incomplete inventory.
    last_error = None
    for delay in (0.5, 1.0, 2.0):
        time.sleep(delay)
        try:
            refresh()
            last_error = None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            last_error = error
    if last_error is not None:
        raise last_error


def main():
    CACHE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if sys.argv[1:] == ["install"]:
        install()
    elif sys.argv[1:] == ["displays"]:
        display_change()
    elif len(sys.argv) == 3 and sys.argv[1] == "window" and sys.argv[2].isdigit():
        process_window(int(sys.argv[2]))
    else:
        raise ValueError("usage: window-memory.py install|displays|window <id>")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        print("window-memory: " + (error.stderr or "").strip(), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print("window-memory: " + str(error), file=sys.stderr)
        sys.exit(1)
