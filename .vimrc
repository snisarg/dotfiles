filetype plugin indent on
syntax on
set number
set smartindent
set autoindent

set shiftwidth=2    " Spaces per indent
set tabstop=2 	    " Spaces per tab in display
set softtabstop=2   " Spaces per tab when inserting
#set expandtab	    " substitute spaces for tabs

set showcmd         " Show normal mode commands as they are entered
set showmode        " Show editing mode in status (-- INSERT --)
set showmatch       " Flash matching delimiters

" Search
set nohlsearch      " don't persist search highlighting
set incsearch       " search with typeahead

set noerrorbells    " no bells in terminal 

set backspace=indent,eol,start     " backspace over everything

set undolevels=1000 " number of undos stored
