# dotfiles
My dotfiles. 

## Yabai

Yabai is configured as a floating window router: it sends applications and
named windows to their assigned macOS Spaces without enabling automatic
tiling. The shared routing rules and per-laptop Space mappings live in
`.yabairc`.

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
  matching repository paths.
- `sync_out.sh` deploys `.yabairc` plus the selected profile and updates the
  machine-local selector.
- `--profile <name>` or `--profile=<name>` overrides the local selector. The
  `YABAI_PROFILE` environment variable remains supported for compatibility.
