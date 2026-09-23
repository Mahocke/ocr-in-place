#!/bin/bash
# Nightly delta run: pick up PDFs that are new or have changed since last time.
#
# Deliberately a single machine. One worker means one consistent set of tools -
# differently aged ocrmypdf and qpdf builds across several machines otherwise
# produce different results depending on who happens to pick up the file.
#
# The inventory only downloads what changed (compared by cTag) and the OCR pass
# only takes the new candidates, so a quiet day costs a few minutes.
set -u
REF="contoso"          # site name, or contoso:Documents/Folder, or drive:<id>

echo "===== delta run $(date '+%Y-%m-%d %H:%M') ====="

echo "-- inventory --"
ocr-in-place scan -r --quiet --walk 6 --worker local:4:1 "$REF"

echo "-- OCR --"
ocr-in-place run --worker local:2:2 --versions 3 --lang deu+eng --yes "$REF"

echo "===== finished $(date '+%H:%M') ====="
