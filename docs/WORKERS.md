# Distributing the work

OCR is CPU-bound. A Raspberry Pi will happily coordinate tens of thousands of
files but takes seconds per page; a modern laptop takes a fraction of one. The
coordinator and the workers are therefore separate roles, and the coordinator
does not have to be fast.

## The worker spec

```
--worker NAME:SLOTS:JOBS
```

- **NAME** — `local`, or anything your `ssh` config can reach
- **SLOTS** — how many files this machine works on at once
- **JOBS** — `ocrmypdf --jobs`, i.e. threads per file

`host-a:2:4` means two files at a time with four threads each, so eight threads
in total. Give several `--worker` flags and they share one queue: whoever
becomes free takes the next file. A slow machine simply takes fewer files, and
a machine that dies holds nothing up.

Leaving a couple of cores free is the difference between "the OCR runs in the
background" and "my laptop is unusable".

## Setting up a worker

The worker is one Python file with no dependencies beyond the standard library
and the OCR tools:

```bash
# on the worker
brew install ocrmypdf            # macOS
sudo apt install ocrmypdf poppler-utils tesseract-ocr-deu   # Debian/Ubuntu
mkdir -p ~/bin
# copy bin/ocr-in-place-worker to ~/bin/ and chmod +x
```

```bash
# on the coordinator
ocr-in-place workers --worker local:1:3 --worker host-a:2:4
```

That prints the full toolchain each machine reports - ocrmypdf, tesseract,
Ghostscript, qpdf, poppler and the platform - and warns when the workers do not
agree, which is the quickest way to find the one that is three releases behind.

Set `OCR_REMOTE_WORKER` if you keep it somewhere other than
`~/bin/ocr-in-place-worker`.

## ssh

The coordinator keeps one ssh connection open per slot for the whole run
(`ControlMaster` / `ControlPersist`). Without that, every single file pays for
a full handshake — across a relayed VPN that was 837 ms per file here, and
227 ms with multiplexing.

Use key authentication and `BatchMode=yes`; the tool will not answer a
password prompt.

## What the network actually carries

Almost nothing. The coordinator sends a job as one line of JSON and gets one
line back. The PDF travels directly between the worker and the cloud over the
pre-authorised URLs. A worker on a fast link is fast even if the coordinator
sits on a slow one.

This is also why it is worth checking *where* a worker really is. Two machines
with identical CPUs measured 0.37 s and 1.29 s per page here — not a difference
in compute, but one of them reached SharePoint through a relayed VPN hop in
another country.

## Keep the tool versions close

Different ocrmypdf and qpdf versions produce different outcomes on damaged
files. qpdf 12 rejects broken JPEG data that qpdf 11 passed through, and
ocrmypdf 17 fails on some files that 16 repairs via the PDF/A path. Nothing
unsafe comes of it — the verification step catches bad output — but you get
results that depend on which machine happened to pick up the file.

For a nightly delta run, use one worker. Reach for the fleet when there is a
backlog to clear.
