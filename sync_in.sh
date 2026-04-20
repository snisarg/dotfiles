#!/usr/bin/env bash
# copy_here.sh — pull dotfiles from home directory into repo
# Usage: ./copy_here.sh [--dry-run|-n] <app...>|all
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

copy_karabiner() {
  run cp ~/.config/karabiner/karabiner.json .config/karabiner/karabiner.json
  run cp -R ~/.config/karabiner/assets/ .config/karabiner/assets/
}

copy_zsh()       { run cp ~/.zshrc .zshrc; }
copy_aerospace() { run cp ~/.aerospace.toml .aerospace.toml; }
copy_bash()      { run cp ~/.bash_profile .bash_profile; }
copy_vim()       { run cp ~/.vimrc .vimrc; }
copy_git()       { run cp ~/.gitconfig .gitconfig; }
copy_inputrc()   { run cp ~/.inputrc .inputrc; }

usage() {
  echo "Usage: $0 [--dry-run|-n] <app...>|all"
  echo "Apps: ${APPS[*]}"
  exit 1
}

[[ -f copy_here.sh ]] || { echo "Error: run from repo root"; exit 1; }

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
