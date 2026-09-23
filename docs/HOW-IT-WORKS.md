# How it works

## The one idea

A PDF is uploaded back onto its **own item ID**, not to its path:

```
POST /drives/<drive>/items/<item>/createUploadSession
     { "item": { "@microsoft.graph.conflictBehavior": "replace" } }
```

Everything that identifies the document survives that: item ID, name, folder,
sharing links, permissions, list metadata, the position in anyone's "recent
files". SharePoint records a new version and nothing else. The original is
version *n*, the searchable copy is version *n+1*, and one click in the version
history brings the original back.

Uploading to the *path* with `replace` would look the same in the UI and is not
the same thing — depending on the library you can end up with a new item ID,
which silently breaks every link that pointed at the old one.

## The pipeline for one file

```
coordinator                      worker
-----------                      ------
resolve REF -> drive + path
walk the folder tree
  (6 threads, one call/folder)
for each PDF:
  fetch downloadUrl  ------------->  download straight from SharePoint
                                     pdfinfo   -> page count
                                     pdfsig    -> signed? then stop
                                     pdftotext -> pages that already have text
                                     ocrmypdf --skip-text
                                     pdfinfo/pdftotext again on the result
                    <--------------  "ready" + before/after numbers
  re-read the cTag
  still unchanged?
  createUploadSession ------------>  upload straight to SharePoint
                    <--------------  new cTag
  write the journal row
```

The document itself never passes through the coordinator. It hands out two
pre-authorised URLs per file — `@microsoft.graph.downloadUrl` and the
`uploadUrl` of an upload session — each valid for that single file and about an
hour. A worker therefore needs no credential of any kind, and a compromised
worker cannot reach a second file.

## Why the worker asks for the upload URL late

Creating the upload session is the coordinator's last chance to check whether
someone else edited the file while OCR was running. So the worker does all its
work first, then stops and asks. The coordinator re-reads the cTag at that
moment; if it changed, it answers with a refusal and the file is left alone and
retried on the next run.

Handing the upload URL out with the job would move that check minutes earlier —
exactly across the window where the risk lives.

## What is deliberately never touched

| | |
|---|---|
| digitally signed PDFs | `pdfsig` finds any embedded signature. OCR rewrites the file and invalidates it. There is no safe version of this. |
| files that already have text on every page | nothing to gain |
| files where OCR finds nothing legible | recorded as `no-gain`; no new version, because an empty text layer is not worth a version |
| anything over `--max-mb` (default 300) | recorded as `oversize` |

## The safety nets in `run`

- **Page count.** The result must have exactly as many pages as the original.
- **Readability.** The result is re-classified with the same tools. If it comes
  back `broken`, nothing is uploaded.
- **Text gain.** The number of pages carrying text must actually have risen.
- **Version counter.** For the first few files (`--versions N`, default 5) the
  version count is read before and after the upload. If it does not rise, the
  library keeps no version history — the original would be gone. The whole run
  aborts on the spot.
- **Concurrent edit.** cTag comparison immediately before the upload, as above.
- **The journal.** Every decision lands in SQLite, so an interrupted run
  resumes instead of starting over, and a second pass does not redo work.

## Why `--output-type pdf`

`ocrmypdf --skip-text --output-type pdf` passes the page content through
untouched and adds nothing but an invisible text layer. Pages are not
re-rendered, so nothing can degrade.

Some old scans contain damaged JPEG data. qpdf refuses to pass those through
and ocrmypdf stops with exit code 4. Only in that case does the worker retry
with `--output-type pdfa`, where Ghostscript rebuilds the page and repairs the
image on the way. Every other exit code stays an error, so nothing questionable
is uploaded. Files repaired that way are marked in the log.

Note that qpdf became stricter about broken JPEGs in version 12; on a machine
with an older qpdf the same file may go straight through. It is worth keeping
the tool versions aligned across workers — see [WORKERS.md](WORKERS.md).

## The timestamp

By default the "Modified" date of the original is preserved. It has to be sent
**inside** the upload session:

```json
{ "item": { "@microsoft.graph.conflictBehavior": "replace",
            "fileSystemInfo": { "lastModifiedDateTime": "..." } } }
```

Patching it afterwards with a separate `PATCH` also works — and creates a
*second* version, which defeats the point. Use `--no-keep-mtime` if you would
rather see when the OCR happened.

## Which versions produced a file

Every processed file records the whole toolchain - ocrmypdf, tesseract,
Ghostscript, qpdf, poppler and the platform - stored once per distinct set in
a `toolchains` table and referenced from the `ocr` row by a short hash.

This is not bookkeeping for its own sake. What survives a damaged scan is
decided by qpdf and Ghostscript at least as much as by ocrmypdf, so
"worker A has ocrmypdf 16.7" does not let you reproduce a result months later.
`ocr-in-place workers` warns when the workers disagree, and `report` lists the
toolchains that did the work.

## Checking afterwards

`audit` samples files the journal calls finished, downloads the current version
and the one before it, and verifies the claim: page count held, text really
present, and a previous version still there to restore. It picks the earlier
version by date rather than by position in the list - Graph makes no promise
about the order, and taking element [1] on faith would mean comparing a file
with itself and calling that a pass.

## Delta runs

`scan` stores a cTag per file. On the next run, anything whose cTag is
unchanged is taken from the journal and never downloaded again. After a
successful upload the journal is updated to the *new* cTag, so our own work
does not look like an external change. That is what keeps a nightly run down to
minutes instead of hours.
