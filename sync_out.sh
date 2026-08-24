#!/usr/bin/env bash
# sync_out.sh — deploy dotfiles from repo to home directory
# Usage: ./sync_out.sh [--dry-run|-n] <app...>|all
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

sync_karabiner() {
  run mkdir -p ~/.config/karabiner/assets
  run rsync -a --delete .config/karabiner/karabiner.json ~/.config/karabiner/karabiner.json
  run rsync -a --delete .config/karabiner/assets/ ~/.config/karabiner/assets/
}

sync_zsh()      { run cp .zshrc ~/.zshrc; }
sync_aerospace() { run cp .aerospace.toml ~/.aerospace.toml; }
sync_bash()     { run cp .bash_profile ~/.bash_profile; }
sync_vim()      { run cp .vimrc ~/.vimrc; }
sync_git()      { run cp .gitconfig ~/.gitconfig; }
sync_inputrc()  { run cp .inputrc ~/.inputrc; }

sync_alttab()     { run defaults import com.lwouis.alt-tab-macos preferences/AltTab.plist; }
sync_ice()        { run defaults import com.jordanbaird.Ice preferences/Ice.plist; }
sync_meetingbar() { run defaults import leits.MeetingBar preferences/MeetingBar.plist; }
sync_shortcat()   { run defaults import com.sproutcube.Shortcat preferences/Shortcat.plist; }

sync_fluidvoice() {
  run mkdir -p ~/Library/Application\ Support/FluidVoice
  run cp preferences/FluidVoice/parakeet_custom_vocabulary.json ~/Library/Application\ Support/FluidVoice/parakeet_custom_vocabulary.json
}

usage() {
  echo "Usage: $0 [--dry-run|-n] <app...>|all"
  echo "Apps: ${APPS[*]}"
  exit 1
}

# Must run from repo root
[[ -f sync_out.sh ]] || { echo "Error: run from repo root"; exit 1; }

[[ $# -eq 0 ]] && usage

# Parse flags
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
    for a in "${APPS[@]}"; do "sync_$a"; done
  elif declare -f "sync_$app" > /dev/null; then
    "sync_$app"
  else
    echo "Unknown app: $app"
    usage
  fi
done
