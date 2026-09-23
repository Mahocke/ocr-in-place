#!/bin/bash
# First pass over a set of personal OneDrives, spread across two workers.
#
# Address each drive by its ID. Deliberately one library per person: the
# PersonalCacheLibrary that sits next to it is an internal OneDrive cache and
# must not be touched. Drives that are locked server-side answer HTTP 423 -
# leave those out rather than fighting them.
#
#   ocr-in-place scan "user@example.com/Documents"     finds the drive by name
#   curl .../users/<upn>/drives                        gives you the IDs
set -u
W=(--worker host-a:4:2 --worker host-b:4:2)

# name|drive id   - smallest drive first, so problems show up early
DRIVES=(
  "Alice|b!REPLACE_WITH_DRIVE_ID"
  "Bob|b!REPLACE_WITH_DRIVE_ID"
)

for entry in "${DRIVES[@]}"; do
  NAME="${entry%%|*}"
  ID="${entry##*|}"
  echo
  echo "############ $NAME - $(date '+%Y-%m-%d %H:%M') ############"
  echo "-- inventory --"
  ocr-in-place scan -r --quiet --walk 6 "${W[@]}" "drive:$ID"
  echo "-- OCR --"
  ocr-in-place run "${W[@]}" --versions 5 --lang deu+eng --yes "drive:$ID"
done

echo
echo "############ all drives finished - $(date '+%Y-%m-%d %H:%M') ############"
