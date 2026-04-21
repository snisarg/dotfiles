#!/usr/bin/env bash
# sync_out.sh — deploy dotfiles from repo to home directory
# Usage: ./sync_out.sh [--dry-run|-n] <app...>|all
# Run from repo root.

set -euo pipefail

APPS=(karabiner zsh aerospace bash vim git inputrc)
DRY_RUN=false

run() {
  if $DRY_RUN; then
    echo "[dry-run] $*"
  else
    "$@"
  fi
}

sync_karabiner() {
  run cp .config/karabiner/karabiner.json ~/.config/karabiner/karabiner.json
  run cp -R .config/karabiner/assets/ ~/.config/karabiner/assets/
}

sync_zsh()      { run cp .zshrc ~/.zshrc; }
sync_aerospace() { run cp .aerospace.toml ~/.aerospace.toml; }
sync_bash()     { run cp .bash_profile ~/.bash_profile; }
sync_vim()      { run cp .vimrc ~/.vimrc; }
sync_git()      { run cp .gitconfig ~/.gitconfig; }
sync_inputrc()  { run cp .inputrc ~/.inputrc; }

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
