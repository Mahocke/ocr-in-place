#!/bin/bash
# Build the corpus and run everything. Takes a few minutes - most of it is
# real OCR on deliberately awkward files, which is the point.
set -eu
cd "$(dirname "$0")/.."

missing=""
for t in ocrmypdf tesseract pdfinfo pdftotext pdfsig pdftoppm qpdf; do
  command -v "$t" >/dev/null || missing="$missing $t"
done
if [ -n "$missing" ]; then
  echo "missing tools:$missing" >&2
  echo "the suite needs ocrmypdf, tesseract, poppler-utils and qpdf" >&2
  exit 2
fi

echo "== building the corpus"
python3 tests/make_corpus.py

echo
echo "== running the tests"
exec python3 -W ignore::ResourceWarning -m unittest discover -s tests -v
