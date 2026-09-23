# Tests

```bash
tests/run-tests.sh
```

A few minutes, most of it real OCR. Needs ocrmypdf, tesseract, poppler-utils
and qpdf on the machine running the tests. Nothing here touches a network
beyond localhost, and no cloud credentials are involved.

## What is being tested, and why

This program replaces documents inside a live document management system. The
suite is built around that: it asserts **safety invariants**, not success.

Whether a damaged file can be repaired depends on the versions of ocrmypdf,
qpdf and Ghostscript on the machine. Whether such a file can ever be silently
replaced by something worse must not depend on anything.

### The corpus

`make_corpus.py` generates eleven awkward PDFs rather than checking binaries
into the repository:

| file | what it is | expected |
|---|---|---|
| `full-text.pdf` | text on every page | `text`, never rewritten |
| `image-scan.pdf` | a pure scan with legible words | `image`, gains text |
| `partial-text.pdf` | page 1 text, page 2 scanned | `partial`, gains text |
| `no-legible-text.pdf` | a scan of pure geometry | `image`, then `no-gain` |
| `broken-xref.pdf` | real text, wrecked cross-reference table | `text` after poppler repairs it |
| `truncated.pdf` | cut off mid-file | `broken` |
| `zero-pages.pdf` | valid PDF, no pages | `broken` |
| `not-a-pdf.pdf` | random bytes behind a PDF header | `broken` |
| `encrypted.pdf` | password protected | `broken`, never rewritten |
| `signed.pdf` | carries a signature field | `signed`, never rewritten |
| `damaged-jpeg.pdf` | corrupted JPEG stream | repaired via PDF/A **or** a clean failure |

The last one behaves differently depending on the qpdf version, which is
exactly why the test accepts either outcome and only forbids a bad upload.

### The fake cloud

`fake_graph.py` answers the handful of Graph endpoints this program uses. It
exists for the three situations that cannot be rehearsed against a real tenant
without risking real documents:

- **somebody saves the file while OCR is running** → the cTag changes, and the
  upload must not happen
- **the library keeps no version history** → the version counter does not rise,
  and the whole run must abort rather than destroy the first original
- **the service is rate limiting** → 429 must be retried and must never be
  recorded as a defect, or the file would be skipped silently for ever

It also keeps a version history per file, so `audit` can be tested against the
actual previous bytes.

## Notes

`test_coordinator.py` imports `bin/ocr-in-place` as a module and points
`graph.GRAPH` at the fake server. No credentials, no Entra, no network.

The suite has already caught real bugs: a `/run` directory that does not exist
on macOS, a coordinator that compared a file against itself when picking the
previous version, and a folder listing that could fail silently and leave
thousands of files unexamined without saying so.
