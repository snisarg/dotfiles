export EDITOR=vim

export CLICOLOR=1
export LSCOLORS=GxFxCxDxBxegedabagaced
export PS1="`printf '\n'`>>> \w `printf '\n$ '`"

# Set key bindings to Vi
set -o vi

# Max history lines in memory
HISTSIZE=130000
# Max history lines on disk 
HISTFILESIZE=-1
# Ignore duplicate lines
export HISTCONTROL=ignoredups:erasedups

# Write history across sessions
shopt -s histappend
# export PROMPT_COMMAND="history -a; history -n"
