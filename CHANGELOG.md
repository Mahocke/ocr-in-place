# Changelog

## 0.1.0 (unreleased)

- Initial public release for SharePoint, OneDrive and Infomaniak kDrive.
- Read-only discovery and classification of PDF files.
- In-place OCR with page-count, validity and text-gain checks before upload.
- Protection for signed PDFs, concurrent edits and missing version history.
- Resumable SQLite journal, audit command, delta runs and optional workers.
- Generated safety corpus covering damaged, encrypted, signed and incomplete PDFs.
- PDF/A fallback converts colours to RGB, so files with unusual colour spaces
  are repaired instead of failing with `ColorConversionNeededError`.

Before publishing: run the CI suite, verify a dry run against a disposable
library, review the documented permissions, and create the `v0.1.0` tag only
after the resulting commit has passed CI.
