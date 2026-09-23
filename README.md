# ocr-in-place

Give scanned PDFs a searchable text layer **inside SharePoint, OneDrive or
kDrive** — as a new version of the same file. No new files, no renamed files,
no broken links, and every original one click away in the version history.

```
OK  Finance/2019/Invoices/scan_0043.pdf  14p  0->14 text pages  2104 -> 1832 kB  v1->v2
```

## Why this exists

Our scanned PDFs were findable in SharePoint. Microsoft indexes them
server-side, and typing a word into the search box brought them up. So the
problem looked solved.

It wasn't. That text only ever lived in Microsoft's index, never in the file.
Everything that touches the document itself still got a picture of paper:

- An AI assistant, asked about a contract, found the file in SharePoint and
  then could not read a word of it. That is what finally pushed me to build
  this.
- The accountant's software. The DATEV export. Any script.
- Ctrl+F in a PDF viewer. Copy and paste. Screen readers.
- And the day you leave Microsoft 365, the index stays behind. The files arrive
  at their new home as unsearchable as the day they were scanned.

An embedded text layer is different: it lives in the document. Copy it to
another cloud, a NAS, a backup drive, hand it to a colleague — it stays
searchable, in any viewer, for anyone and anything that opens it.

So this tool writes the text into the documents themselves, in place, without
creating a single new file and without losing a single original.

## What it does

1. **Walks** a site, a library, a folder or somebody's OneDrive.
2. **Classifies** every PDF — already has text / partly / pure scan / digitally
   signed / unreadable — by downloading it once and looking inside.
3. **Runs OCR** on the candidates with `ocrmypdf --skip-text`, which adds an
   invisible text layer and leaves the page content untouched.
4. **Verifies** the result: same page count, still a valid PDF, text actually
   present now.
5. **Writes it back onto the same item ID.** SharePoint records a new version.
   Name, path, sharing links, permissions and metadata are unchanged.

Everything lands in a SQLite journal, so an interrupted run resumes, a second
pass does not repeat work, and a nightly delta run only touches what changed.

## Quick start

```bash
# Debian/Ubuntu
sudo apt install ocrmypdf poppler-utils tesseract-ocr-deu python3

git clone https://github.com/Mahocke/ocr-in-place
cd ocr-in-place
sudo install -d -m 700 /etc/ocr-in-place
sudo cp examples/config.ini.sample /etc/ocr-in-place/config.ini
sudo chmod 600 /etc/ocr-in-place/config.ini
sudo editor /etc/ocr-in-place/config.ini     # tenant, client id, secret

# read-only: what is actually in there?
sudo ./bin/ocr-in-place scan -r contoso/Finance

# do it
sudo ./bin/ocr-in-place run -r --lang deu+eng --yes contoso/Finance
```

Registering the application takes about five minutes and is described in
[docs/SETUP-SHAREPOINT.md](docs/SETUP-SHAREPOINT.md). `scan` never writes
anything; `run` refuses to start without `--yes`.

To install it properly:

```bash
sudo install -m 755 bin/ocr-in-place bin/ocr-in-place-worker \
                    bin/ocr-in-place-web bin/ocr-in-place-kdrive /usr/local/bin/
sudo install -d /usr/local/lib/ocr-in-place
sudo install -m 644 lib/graph.py /usr/local/lib/ocr-in-place/
```

## Commands

| | |
|---|---|
| `scan REF [-r]` | classify every PDF. Read only. |
| `run REF [-r] --yes` | OCR the candidates, write back as a new version |
| `run ... --dry-run` | everything except the upload |
| `workers --worker ...` | reachable? is ocrmypdf there? which version? |
| `report [REF]` | what has been scanned and processed so far |
| `refresh REF [-r]` | update stored cTags, metadata only |
| `audit [REF] [--all]` | re-check finished files against their previous version |

`REF` is a site, a library, a path, a user's OneDrive, a drive ID or the URL
from your browser's address bar — see
[docs/SETUP-SHAREPOINT.md](docs/SETUP-SHAREPOINT.md#addressing-files).

## Safety

This tool rewrites documents in a live document management system. Everything
below exists because that deserves respect:

- **Digitally signed PDFs are never touched.** OCR would invalidate the
  signature. `pdfsig` detects them and the file is left alone.
- **The version counter is checked** on the first few files. If it does not
  rise, the library keeps no version history — the original could not be
  restored — and the entire run aborts immediately.
- **Concurrent edits lose to the user.** The cTag is re-read in the moment
  before the upload. If somebody else changed the file while OCR was running,
  nothing is uploaded and the file is picked up next time.
- **Bad output is never uploaded.** Page count, validity and text gain are all
  verified first.
- **Nothing is deleted, ever.** The tool has no code path that removes a file.
- **`run` requires `--yes`.** `scan` and `--dry-run` cannot write.

Do a `--dry-run` on a folder first. Look at the version history of the first
file afterwards. Then let it loose.

### Checking afterwards

The tests protect future runs. `audit` protects the past: it takes files the
journal calls finished, fetches the current version **and the one before it**,
and verifies that what was claimed actually happened.

```bash
ocr-in-place audit --sample 50          # a random sample
ocr-in-place audit --all contoso        # everything under one site
```

It reports a file whose page count changed, whose text is not actually there,
or - the one that matters most - whose version history has quietly gone, so
the original could no longer be restored. Exit code 1 if anything is wrong.
This reads PDFs on the coordinator, so poppler-utils has to be installed there.

### Reproducibility

Every processed file records the **whole toolchain** that produced it, not just
ocrmypdf: tesseract, Ghostscript, qpdf, poppler and the platform, stored once
per distinct set and referenced by a short hash.

```
$ ocr-in-place workers --worker local:1:2 --worker host-a:4:2
OK   local (1x2)   ocrmypdf=16.7.0  tesseract=5.5.0  ghostscript=10.05.1  qpdf=12.2.0 ...
OK   host-a (4x2)  ocrmypdf=17.12.1 tesseract=5.5.3  ghostscript=10.08.0  qpdf=12.4.1 ...

WARNING: the workers do not agree on their tool versions. The outcome for a
damaged file then depends on which machine happens to pick it up.
```

That warning is not theoretical: qpdf 12 rejects damaged JPEG data that qpdf 11
passed through, and the two machines above genuinely disagree about a handful
of old scans.

## Spreading the work

OCR is CPU-bound; walking a tree of 70,000 files is not. A Raspberry Pi is a
perfectly good coordinator if the computing happens elsewhere:

```bash
ocr-in-place run -r --yes \
  --worker local:1:2 --worker host-a:4:2 --worker host-b:4:2 contoso
```

Workers share one queue — whoever is free takes the next file. **No worker ever
sees a credential.** Per file the coordinator hands out two pre-authorised URLs
that are valid for that one file and about an hour; the worker downloads from
the cloud, computes, and uploads back to the cloud directly. The document never
passes through the coordinator.

See [docs/WORKERS.md](docs/WORKERS.md).

## Other clouds

`ocr-in-place-kdrive` does the same thing for Infomaniak kDrive: upload against
an existing `file_id`, same file, new version. It is a separate, simpler
program because kDrive hands out no pre-authorised URLs, so there is nothing to
delegate to a worker. See [docs/SETUP-KDRIVE.md](docs/SETUP-KDRIVE.md).

Adding a third cloud means implementing four things: list a folder, get a
change token, download a file, replace a file by its ID. If your storage can do
those, this pattern fits.

## Progress page

```bash
sudo install -m 644 systemd/ocr-in-place-web.service /etc/systemd/system/
sudo systemctl enable --now ocr-in-place-web
```

A single page on port 8099 with classification tiles, throughput per worker and
the last 25 files. Read-only from the SQLite journal, bound to localhost by
default — it has no authentication and shows file paths.

There is also a delta-run timer in `systemd/` and two example runner scripts in
`examples/`.

## What it does not do

- It does not improve existing text layers. `--skip-text` means pages that
  already carry text are passed through as they are.
- It does not handle anything but PDFs.
- It has no web UI for starting runs. It is a command-line tool with a
  read-only status page.
- It does not deduplicate, rename, sort or otherwise tidy your archive.
- It cannot do anything about a library that has version history turned off,
  except refuse to run.

## Tests

```bash
tests/run-tests.sh
```

Thirty-three tests against a generated corpus of deliberately awkward PDFs -
damaged JPEG, broken xref, truncated, encrypted, zero pages, signed - and a
fake Graph server for the three situations you cannot rehearse against a live
tenant: a file edited while OCR is running, a library with no version history,
and a service that is rate limiting.

The tests assert safety invariants rather than success. See
[tests/README.md](tests/README.md).

## Requirements

Python 3.9+ and the standard library — no pip packages. Plus `ocrmypdf`,
`tesseract` (with the language packs you need) and `poppler-utils` on the
machines that do the work. Tested on Debian 13 and macOS 15.

## About support

I built this for our own archive and it has worked well here — tens of
thousands of documents, in place, no losses. I am putting it out in case it
saves somebody else the same week of work.

But I should be straight with you: this is not a product and I do not have the
time to look after it like one. I will probably not answer issues, review pull
requests or fix things that do not affect me. Please do not build a business on
it and then be disappointed in me.

What you can do: fork it, read it — it is small on purpose — and change it
until it fits your setup. Run `scan` first, run `--dry-run` second, and check
the version history of the first file before you trust it with the rest. If it
helps, I am glad. If it breaks something, that is on your backups and mine is
the code you were free to read.

## License

MIT — see [LICENSE](LICENSE).

This project contains no PDF or image code of its own. It orchestrates existing
tools as separate processes and bundles none of them; you install those
yourself, under their own licenses:

| Tool | License |
|---|---|
| OCRmyPDF | MPL-2.0 |
| Tesseract, Leptonica | Apache-2.0 / BSD |
| qpdf, pikepdf | Apache-2.0 / MPL-2.0 |
| poppler-utils | GPL-2/3 |
| Ghostscript | AGPL-3.0 |

Ghostscript being AGPL matters if you ever expose this as a public network
service — Artifex sells commercial licenses for that case. For in-house use
there is nothing to do. Note that ocrmypdf only reaches for Ghostscript on the
PDF/A fallback path; the normal route goes through qpdf.
