#!/bin/bash
osascript -e 'set volume output volume 80'
for i in $(seq 1 8); do
  afplay /System/Library/Sounds/Glass.aiff
  osascript -e 'display notification "Wake up bro, it is 9 AM!" with title "Spark Alarm" sound name "Glass"'
  sleep 1
done