#!/usr/bin/env bash
# sync_out.sh — deploy dotfiles from repo to home directory
# Usage: ./sync_out.sh [--dry-run|-n] [--profile <name>] <app...>|all
# Run from repo root.

set -euo pipefail

APPS=(karabiner zsh aerospace yabai bash vim git inputrc alttab thaw meetingbar shortcat fluidvoice)
DRY_RUN=false
PROFILE="${YABAI_PROFILE:-}"

run() {
  if $DRY_RUN; then
    echo "[dry-run] $*"
  else
    "$@"
  fi
}

sync_karabiner() {
  run mkdir -p ~/.config/karabiner/assets
  run rsync -a --delete .config/karabiner/karabiner.json ~/.config/karabiner/karabiner.json
  run rsync -a --delete .config/karabiner/assets/ ~/.config/karabiner/assets/
}

sync_zsh()      { run cp .zshrc ~/.zshrc; }
sync_aerospace() { run cp .aerospace.toml ~/.aerospace.toml; }
resolve_yabai_profile() {
  local profile=$PROFILE

  if [[ -z $profile && -f ~/.config/yabai/profile ]]; then
    profile=$(sed -n '1p' ~/.config/yabai/profile)
  fi

  if [[ -z $profile ]]; then
    echo "Error: no Yabai profile selected; use --profile <name>" >&2
    return 1
  fi
  if [[ ! $profile =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "Error: invalid Yabai profile name: $profile" >&2
    return 1
  fi

  printf '%s\n' "$profile"
}

sync_yabai() {
  local profile source_profile
  profile=$(resolve_yabai_profile)
  source_profile=".config/yabai/profiles/$profile.sh"

  if [[ ! -f $source_profile ]]; then
    echo "Error: Yabai profile config not found: $source_profile" >&2
    return 1
  fi

  run mkdir -p ~/.config/yabai/profiles
  run cp .yabairc ~/.yabairc
  run chmod +x ~/.yabairc
  run cp "$source_profile" "$HOME/.config/yabai/profiles/$profile.sh"

  if $DRY_RUN; then
    echo "[dry-run] write Yabai profile '$profile' to ~/.config/yabai/profile"
  else
    printf '%s\n' "$profile" > ~/.config/yabai/profile
  fi
  echo "Deployed Yabai profile: $profile"
}
sync_bash()     { run cp .bash_profile ~/.bash_profile; }
sync_vim()      { run cp .vimrc ~/.vimrc; }
sync_git()      { run cp .gitconfig ~/.gitconfig; }
sync_inputrc()  { run cp .inputrc ~/.inputrc; }

sync_alttab()     { run defaults import com.lwouis.alt-tab-macos preferences/AltTab.plist; }
sync_thaw()       { run defaults import com.stonerl.Thaw preferences/Thaw.plist; }
sync_meetingbar() { run defaults import leits.MeetingBar preferences/MeetingBar.plist; }
sync_shortcat()   { run defaults import com.sproutcube.Shortcat preferences/Shortcat.plist; }

sync_fluidvoice() {
  run mkdir -p ~/Library/Application\ Support/FluidVoice
  run cp preferences/FluidVoice/parakeet_custom_vocabulary.json ~/Library/Application\ Support/FluidVoice/parakeet_custom_vocabulary.json
}

usage() {
  echo "Usage: $0 [--dry-run|-n] [--profile <name>] <app...>|all"
  echo "Apps: ${APPS[*]}"
  exit 1
}

# Must run from repo root
[[ -f sync_out.sh ]] || { echo "Error: run from repo root"; exit 1; }

[[ $# -eq 0 ]] && usage

args=()
while [[ $# -gt 0 ]]; do
  case $1 in
    --dry-run|-n)
      DRY_RUN=true
      shift
      ;;
    --profile)
      [[ $# -ge 2 ]] || usage
      PROFILE=$2
      shift 2
      ;;
    --profile=*)
      PROFILE=${1#*=}
      shift
      ;;
    *)
      args+=("$1")
      shift
      ;;
  esac
done

[[ ${#args[@]} -eq 0 ]] && usage

for app in "${args[@]}"; do
  if [[ $app == all ]]; then
    for a in "${APPS[@]}"; do "sync_$a"; done
  elif declare -f "sync_$app" > /dev/null; then
    "sync_$app"
  else
    echo "Unknown app: $app"
    usage
  fi
done
