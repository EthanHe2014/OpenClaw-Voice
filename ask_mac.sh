#!/bin/bash
# ask_mac.sh — pop a native macOS prompt with choices and return the selection.
#
# Usage:
#   ask_mac.sh "Question text" "Choice A" "Choice B" "Choice C"
#   ask_mac.sh --notify "Title" "Message"            # plain notification, no choices
#   ask_mac.sh --list  "Question" "a" "b" "c" "d"    # >3 choices -> scrollable picker
#
# Prints the chosen value on stdout; exits non-zero if cancelled/timed out.

set -euo pipefail

if [ "$1" = "--notify" ]; then
  title="${2:-Spark}"; msg="${3:-}"
  osascript -e "display notification \"$msg\" with title \"$title\" sound name \"Glass\""
  exit 0
fi

if [ "$1" = "--list" ]; then
  question="$2"; shift 2
  # Build an AppleScript list literal: {"a","b","c"}
  items=""
  for c in "$@"; do
    esc="${c//\\/\\\\}"; esc="${esc//\"/\\\"}"
    items="$items\"$esc\","
  done
  items="${items%,}"
  osascript <<APPLESCRIPT
set theList to {$items}
set choice to choose from list theList with prompt "$question" OK button name "OK" cancel button name "Cancel"
if choice is false then
  return "CANCELLED"
else
  return item 1 of choice
end if
APPLESCRIPT
  exit 0
fi

QUESTION="$1"; shift
# macOS dialogs allow max 3 buttons.
n=$#
if [ "$n" -eq 0 ]; then echo "no choices given" >&2; exit 2; fi
if [ "$n" -gt 3 ]; then echo "use --list for more than 3 choices" >&2; exit 2; fi

BTNS=""
DEFAULT=""
for c in "$@"; do
  esc="${c//\\/\\\\}"; esc="${esc//\"/\\\"}"
  BTNS="$BTNS\"$esc\", "
done
BTNS="${BTNS%, }"
DEFAULT="$1"
DEF_ESC="${DEFAULT//\\/\\\\}"; DEF_ESC="${DEF_ESC//\"/\\\"}"

osascript <<APPLESCRIPT
try
  set res to display dialog "$QUESTION" with title "⚡ Spark" buttons {$BTNS} default button "$DEF_ESC" giving up after 300
  if gave up of res then
    return "TIMEOUT"
  else
    return button returned of res
  end if
on error errMsg number errNum
  return "CANCELLED"
end try
APPLESCRIPT
