#!/usr/bin/env python3
"""Remember live windows, not preset grids or ambiguous title-based templates.

Only a display/usable-area change restores geometry. Manual moves/resizes on
the same display update the saved geometry. New windows use their app's layout.
"""
import fcntl
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

MODES = {
    "iTerm2": "fixed",
    "ChatGPT": "fixed",
    "Google Chrome": "fixed",
}
APP_FILTER = "^(iTerm2|ChatGPT|Google Chrome)$"
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


def eligible(window):
    return (
        window.get("app") in MODES
        and window.get("subrole") == "AXStandardWindow"
        and window.get("can-move")
        and window.get("can-resize")
        and window.get("has-ax-reference")
        and not window.get("is-native-fullscreen")
        and not window.get("has-fullscreen-zoom")
        and not window.get("is-minimized")
        and not window.get("is-hidden")
    )


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


def proportions(frame, bounds, mode):
    return {
        "x": (horizontal_alignment(frame, bounds) if mode == "fixed"
              else (frame["x"] - bounds["x"]) / bounds["w"]),
        "y": (frame["y"] - bounds["y"]) / bounds["h"],
        "w": frame["w"] / bounds["w"] if mode == "relative" else frame["w"],
        "h": frame["h"] / bounds["h"],
    }


def target_frame(saved, bounds, mode):
    # Preserve unclamped preferences: a small screen or app minimum size must
    # not permanently replace the user's larger-screen layout.
    width = saved["w"] * bounds["w"] if mode == "relative" else saved["w"]
    width = max(1, min(round(width), bounds["w"]))
    height = max(1, min(round(saved["h"] * bounds["h"]), bounds["h"]))
    horizontal_span = bounds["w"] - width if mode == "fixed" else bounds["w"]
    return {
        "x": round(bounds["x"] + max(0, min(
            saved["x"] * horizontal_span, bounds["w"] - width))),
        "y": round(bounds["y"] + max(0, min(
            saved["y"] * bounds["h"], bounds["h"] - height))),
        "w": width,
        "h": height,
    }


def identity(window):
    return [window["app"], window["pid"]]


def new_record(window, display):
    return {
        "identity": identity(window),
        "display": display,
        "last_frame": window["frame"],
        "horizontal_position": ("available-space" if MODES[window["app"]] == "fixed"
                                else "screen"),
        "proportions": proportions(
            window["frame"], display["frame"], MODES[window["app"]]),
    }


def migrate_record(record, mode):
    """Convert legacy screen-relative x using its last saved physical layout.

    Don't use the live window here: macOS may already have moved it to another
    screen. Preserve the original width, height and vertical preferences.
    """
    if mode != "fixed" or record.get("horizontal_position") == "available-space":
        return record
    saved = dict(record["proportions"])
    saved["x"] = horizontal_alignment(record["last_frame"], record["display"]["frame"])
    return dict(record, proportions=saved, horizontal_position="available-space")


def updated_record(record, window, display):
    """Only manually changed axes replace saved (possibly clamped) preferences."""
    result = dict(record)
    saved = dict(record["proportions"])
    fresh = proportions(window["frame"], display["frame"], MODES[window["app"]])
    for axis in ("x", "y", "w", "h"):
        if abs(window["frame"][axis] - record["last_frame"][axis]) > EPSILON:
            saved[axis] = fresh[axis]
    if MODES[window["app"]] == "fixed":
        if display["frame"]["w"] - window["frame"]["w"] <= EPSILON:
            # Full-width/clamped frames cannot tell us a new alignment.
            saved["x"] = record["proportions"]["x"]
        elif abs(window["frame"]["w"] - record["last_frame"]["w"]) > EPSILON:
            # Resizing changes the available travel even if x didn't move.
            saved["x"] = fresh["x"]
    result.update(proportions=saved, last_frame=window["frame"], display=display)
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


def restore(window, target, bounds):
    window_id = str(window["id"])
    # Move first so the app adopts the destination screen's size limits.
    # Otherwise Electron can reject growth while still associated with the
    # smaller source display, even after yabai has transferred its Space.
    run(YABAI, "-m", "window", window_id, "--move",
        "abs:{x}:{y}".format(**target))
    time.sleep(0.1)
    run(YABAI, "-m", "window", window_id, "--resize",
        "abs:{w}:{h}".format(**target))
    time.sleep(0.1)
    resized = query("--windows", "--window", window_id)["frame"]
    # The app may have a larger minimum size than the requested target.
    desired_x = target["x"]
    requested_travel = bounds["w"] - target["w"]
    if MODES[window["app"]] == "fixed" and requested_travel > EPSILON:
        # Preserve alignment if the app rounds/constrains the requested width.
        alignment = horizontal_alignment(target, bounds)
        desired_x = bounds["x"] + alignment * max(0, bounds["w"] - resized["w"])
    x = round(max(bounds["x"], min(desired_x,
                  bounds["x"] + bounds["w"] - resized["w"])))
    y = round(max(bounds["y"], min(target["y"],
                  bounds["y"] + bounds["h"] - resized["h"])))
    run(YABAI, "-m", "window", window_id, "--move",
        "abs:%s:%s" % (x, y))
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
            if record is not None and record["identity"] == identity(window):
                record = migrate_record(record, MODES[window["app"]])
            if record is None or record["identity"] != identity(window):
                state[key] = new_record(window, display)
            elif record["display"] != display:
                target = target_frame(record["proportions"], display["frame"],
                                      MODES[window["app"]])
                if not close_frame(window["frame"], target):
                    actual = restore(window, target, display["frame"])
                    if actual["display"] != window["display"]:
                        # Topology changed again mid-operation. Preserve the
                        # original record for the next display/focus event.
                        return
                    window = actual
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
    for window in windows:
        if eligible(window):
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
            key: migrate_record(value, MODES.get(value["identity"][0]))
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
        run(YABAI, "-m", "signal", "--add", "label=" + label,
            "event=" + event, "app=" + APP_FILTER,
            "action=" + command + ' window "$YABAI_WINDOW_ID"')
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
