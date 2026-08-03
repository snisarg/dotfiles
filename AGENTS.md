# AGENTS.md

## Karabiner

Config lives in two places:

- `.config/karabiner/assets/complex_modifications/*.json` — source-of-truth rule definitions, one file per logical rule set (e.g. `home_row_mods.json`, `chars_tap_hold.json`, `f_keys_tap_hold.json`, `caps_lock_pressed.json`). These are importable templates for the Karabiner-Elements GUI and tolerate `//` and `/* */` comments.
- `.config/karabiner/karabiner.json` — the actual active profile that Karabiner-Elements reads and runs. **Never hand-edit this file.** The running app owns it and can rewrite/reload it at any time, so manual edits can be silently clobbered (this is almost certainly what caused `karabiner.json` to revert mid-session in the past — the app overwrote a hand-edited version it didn't know about). The only supported way to get a rule into this file is importing it through the Karabiner-Elements UI.

### Process for making a change

1. Run `./sync_in.sh karabiner` first to pull any local/live changes into the repo (including anything changed via the Karabiner-Elements UI directly), so they aren't lost in step 3.
2. Edit **only** the relevant asset file(s) under `assets/complex_modifications/`. Do not touch `karabiner.json`.
3. Validate the asset file's JSON (strip comments first since it isn't strict JSON):
   `python3 -c "import json, re; s=open(path).read(); s=re.sub(r'/\*.*?\*/','',s,flags=re.S); s=re.sub(r'^\s*//.*$','',s,flags=re.M); json.loads(s)"`
4. Sanity-check the diff (`git diff --stat`) to confirm only the intended change landed.
5. Run `./sync_out.sh karabiner` — this `rsync`s the `assets/` dir (and the repo's current `karabiner.json`) into `~/.config/karabiner/`, so the updated asset file is available to import.
6. **Ask the user to manually import the changed rule via the Karabiner-Elements UI** (Preferences → Complex Modifications → Add rule → pick it from the updated asset file). This is what formally regenerates `karabiner.json` with the new rule, written by the app itself.
7. Once the user confirms the import is done, run `./sync_in.sh karabiner` again to pull the app-written `karabiner.json` back into the repo, so it can be reviewed/committed.

### Timing/threshold notes

- The active profile sets a global default hold threshold via `complex_modifications.parameters.basic.to_if_held_down_threshold_milliseconds` (currently 200ms). This applies to every manipulator that doesn't override it — including `home_row_mods.json`'s dual-role letters (a/s/d/f/j/k/l/;).
- Per-manipulator overrides are possible by adding a `"parameters"` object directly on that manipulator, e.g. `{ "basic.to_if_held_down_threshold_milliseconds": 400 }`. This is the only way to give a subset of keys a different threshold — there's no rule-level or file-level scope, only global (profile) or per-manipulator.
- Don't bump the global default to change one file's feel — it silently changes every other rule that relies on the default, including home row mods.
