#!/usr/bin/env bash
# sync_in.sh — pull dotfiles from home directory into repo
# Usage: ./sync_in.sh [--dry-run|-n] <app...>|all
# Run from repo root.

set -euo pipefail

APPS=(karabiner zsh aerospace bash vim git inputrc alttab ice meetingbar shortcat fluidvoice)
DRY_RUN=false

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
copy_bash()      { run cp ~/.bash_profile .bash_profile; }
copy_vim()       { run cp ~/.vimrc .vimrc; }
copy_git()       { run cp ~/.gitconfig .gitconfig; }
copy_inputrc()   { run cp ~/.inputrc .inputrc; }

copy_alttab()     { run mkdir -p preferences; run defaults export com.lwouis.alt-tab-macos preferences/AltTab.plist; }
copy_ice()        { run mkdir -p preferences; run defaults export com.jordanbaird.Ice preferences/Ice.plist; }
copy_meetingbar() { run mkdir -p preferences; run defaults export leits.MeetingBar preferences/MeetingBar.plist; }
copy_shortcat()   { run mkdir -p preferences; run defaults export com.sproutcube.Shortcat preferences/Shortcat.plist; }

copy_fluidvoice() {
  run mkdir -p preferences/FluidVoice
  run cp ~/Library/Application\ Support/FluidVoice/parakeet_custom_vocabulary.json preferences/FluidVoice/parakeet_custom_vocabulary.json
}

usage() {
  echo "Usage: $0 [--dry-run|-n] <app...>|all"
  echo "Apps: ${APPS[*]}"
  exit 1
}

[[ -f sync_in.sh ]] || { echo "Error: run from repo root"; exit 1; }

[[ $# -eq 0 ]] && usage

args=()
for arg in "$@"; do
  case $arg in
    --dry-run|-n) DRY_RUN=true ;;
    *) args+=("$arg") ;;
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
