#!/usr/bin/env bash
# sync_in.sh — pull dotfiles from home directory into repo
# Usage: ./sync_in.sh [--dry-run|-n] [--profile <name>] <app...>|all
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

copy_karabiner() {
  run mkdir -p .config/karabiner/assets
  run rsync -a --delete ~/.config/karabiner/karabiner.json .config/karabiner/karabiner.json
  run rsync -a --delete ~/.config/karabiner/assets/ .config/karabiner/assets/
}

copy_zsh()       { run cp ~/.zshrc .zshrc; }
copy_aerospace() { run cp ~/.aerospace.toml .aerospace.toml; }
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

copy_yabai() {
  local profile source_profile
  profile=$(resolve_yabai_profile)
  source_profile="$HOME/.config/yabai/profiles/$profile.sh"

  if [[ ! -f $source_profile ]]; then
    echo "Error: Yabai profile config not found: $source_profile" >&2
    return 1
  fi

  run mkdir -p .config/yabai/profiles
  run cp ~/.yabairc .yabairc
  run cp "$source_profile" ".config/yabai/profiles/$profile.sh"
  echo "Synced Yabai profile: $profile"
}
copy_bash()      { run cp ~/.bash_profile .bash_profile; }
copy_vim()       { run cp ~/.vimrc .vimrc; }
copy_git()       { run cp ~/.gitconfig .gitconfig; }
copy_inputrc()   { run cp ~/.inputrc .inputrc; }

copy_alttab()     { run mkdir -p preferences; run defaults export com.lwouis.alt-tab-macos preferences/AltTab.plist; }
copy_thaw()       { run mkdir -p preferences; run defaults export com.stonerl.Thaw preferences/Thaw.plist; }
copy_meetingbar() { run mkdir -p preferences; run defaults export leits.MeetingBar preferences/MeetingBar.plist; }
copy_shortcat()   { run mkdir -p preferences; run defaults export com.sproutcube.Shortcat preferences/Shortcat.plist; }

copy_fluidvoice() {
  run mkdir -p preferences/FluidVoice
  run cp ~/Library/Application\ Support/FluidVoice/parakeet_custom_vocabulary.json preferences/FluidVoice/parakeet_custom_vocabulary.json
}

usage() {
  echo "Usage: $0 [--dry-run|-n] [--profile <name>] <app...>|all"
  echo "Apps: ${APPS[*]}"
  exit 1
}

[[ -f sync_in.sh ]] || { echo "Error: run from repo root"; exit 1; }

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
    for a in "${APPS[@]}"; do "copy_$a"; done
  elif declare -f "copy_$app" > /dev/null; then
    "copy_$app"
  else
    echo "Unknown app: $app"
    usage
  fi
done
