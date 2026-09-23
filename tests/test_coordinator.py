#!/usr/bin/env python3
"""Coordinator tests against the fake Graph.

This is where the two nets that protect the originals actually get exercised:
the cTag check immediately before the upload, and the version counter that
aborts the run when a library keeps no history. Neither can be rehearsed
against a live tenant without putting real documents at risk.

    python3 tests/make_corpus.py
    python3 -m unittest discover -s tests -v
"""
import argparse
import io
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout, redirect_stderr

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
CORPUS = os.path.join(HERE, "corpus")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))

import fake_graph                                    # noqa: E402
import graph                                         # noqa: E402
from harness import load, page_count, text_page_count  # noqa: E402


def namespace(**kw):
    return argparse.Namespace(**kw)


SCAN = dict(recursive=True, csv=None, parallel=2, walk=2, worker=["local:2:2"],
            max_mb=300, rescan=False, quiet=True)
RUN = dict(recursive=True, worker=["local:2:2"], yes=True, dry_run=False, limit=0,
           only_image=False, rewalk=False, walk=2, lang="eng", nice=10,
           versions=5, timeout=600, max_mb=300, pdfa=False, keep_mtime=True)


class CoordinatorCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(CORPUS) or not os.listdir(CORPUS):
            raise unittest.SkipTest("no corpus - run tests/make_corpus.py first")
        graph.token = lambda: "test-token"          # no Entra in the loop
        cls.coord = load(os.path.join(ROOT, "bin", "ocr-in-place"), "coord")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ocr-test-")
        c = self.coord
        c.DB = os.path.join(self.tmp, "sharepoint.sqlite")
        c.WORK = os.path.join(self.tmp, "cache")
        c.LOCK = os.path.join(self.tmp, "run.lock")
        c._tls = threading.local()                  # drop cached connections
        self.cloud = fake_graph.Cloud()
        base, self.stop = fake_graph.serve(self.cloud)
        graph.GRAPH = base
        for name in sorted(os.listdir(CORPUS)):
            if name.endswith(".pdf"):
                self.cloud.add("Finance/" + name, os.path.join(CORPUS, name))

    def tearDown(self):
        self.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers ----

    def scan(self, **over):
        a = namespace(ref="drive:d1", **{**SCAN, **over})
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.coord.cmd_scan(a)

    def run_ocr(self, **over):
        a = namespace(ref="drive:d1", **{**RUN, **over})
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            self.coord.cmd_run(a)
        return buf.getvalue()

    def puts(self):
        return [e for e in self.cloud.log if e[0] == "put"]

    def uploaded_names(self):
        return sorted(self.cloud.items[i]["name"] for _, i, _ in self.puts())

    def db(self):
        return sqlite3.connect(self.coord.DB)

    def name_of(self, item_id):
        return self.cloud.items[item_id]["name"]

    # ---- scan ----

    def test_scan_is_read_only(self):
        self.scan()
        self.assertEqual(self.puts(), [], "scan must never write")
        self.assertEqual(self.cloud.sessions, 0, "scan must open no upload session")

    def test_scan_classifies_the_corpus(self):
        self.scan()
        rows = dict(self.db().execute("select name, status from items"))
        self.assertEqual(rows["signed.pdf"], "signed")
        self.assertEqual(rows["full-text.pdf"], "text")
        self.assertEqual(rows["image-scan.pdf"], "image")
        self.assertEqual(rows["partial-text.pdf"], "partial")
        for broken in ("zero-pages.pdf", "not-a-pdf.pdf", "truncated.pdf",
                       "encrypted.pdf"):
            self.assertEqual(rows[broken], "broken", broken)

    def test_scan_survives_throttling(self):
        # This is the production incident that cost the most: 994 throttled
        # files stored as "broken" would have been skipped silently for ever.
        self.cloud.throttle_metadata = 5
        self.scan()
        n = self.db().execute("select count(*) from items").fetchone()[0]
        self.assertGreater(n, 5, "429 must be retried, not dropped")
        broken = self.db().execute(
            "select count(*) from items where status='broken' and note like '%429%'"
        ).fetchone()[0]
        self.assertEqual(broken, 0, "a throttled file must never be stored as broken")

    def test_unlistable_folder_is_reported(self):
        # A folder listing that fails for good means those files were never
        # looked at. The summary has to say so - silence would be a lie.
        self.cloud.throttle_listing = 99
        a = namespace(ref="drive:d1", **{**SCAN, "quiet": True})
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            self.coord.cmd_scan(a)
        out = buf.getvalue()
        self.assertIn("could not be listed", out)
        self.assertIn("NOT examined", out)

    # ---- run ----

    def test_dry_run_uploads_nothing(self):
        self.scan()
        self.run_ocr(dry_run=True, yes=False)
        self.assertEqual(self.puts(), [], "--dry-run must not upload")

    def test_run_touches_only_candidates(self):
        self.scan()
        self.run_ocr()
        touched = self.uploaded_names()
        self.assertIn("image-scan.pdf", touched)
        self.assertIn("partial-text.pdf", touched)
        for never in ("signed.pdf", "full-text.pdf", "broken-xref.pdf",
                      "zero-pages.pdf", "not-a-pdf.pdf", "truncated.pdf",
                      "encrypted.pdf", "no-legible-text.pdf"):
            self.assertNotIn(never, touched, never + " must never be rewritten")

    def test_upload_keeps_pages_and_gains_text(self):
        self.scan()
        self.run_ocr()
        self.assertTrue(self.puts(), "nothing was uploaded at all")
        for _, item, _ in self.puts():
            with self.subTest(file=self.name_of(item)):
                after = os.path.join(self.tmp, "after.pdf")
                open(after, "wb").write(self.cloud.content[item])
                before = os.path.join(CORPUS, self.name_of(item))
                self.assertEqual(page_count(after), page_count(before),
                                 "page count changed")
                self.assertGreater(text_page_count(after), text_page_count(before),
                                   "no text was gained")

    def test_version_rises_for_every_upload(self):
        self.scan()
        self.run_ocr()
        for _, item, _ in self.puts():
            self.assertGreaterEqual(self.cloud.versions[item], 2,
                                    "the original must survive as a version")

    def test_modified_date_is_preserved(self):
        self.scan()
        self.run_ocr()
        for _, item, _ in self.puts():
            self.assertEqual(self.cloud.items[item]["lastModifiedDateTime"],
                             "2019-05-04T10:00:00Z",
                             "the timestamp must travel with the upload")

    def test_no_keep_mtime_lets_the_date_move(self):
        self.scan()
        self.run_ocr(keep_mtime=False)
        self.assertTrue(self.puts())

    # ---- the two nets ----

    def test_concurrent_edit_blocks_the_upload(self):
        # Somebody saves the file while OCR is running.
        self.scan()
        self.cloud.change_ctag_on_download = True
        out = self.run_ocr(rewalk=True)
        self.assertEqual(self.puts(), [],
                         "a file changed during OCR must not be overwritten")
        self.assertIn("changed in the meantime", out)

    def test_run_aborts_when_versions_do_not_rise(self):
        # A library without version history: the original would be gone.
        self.scan()
        self.cloud.freeze_versions = True
        out = self.run_ocr(versions=99)
        self.assertIn("ABORTING", out)
        self.assertLessEqual(len(self.puts()), 1,
                             "the run must stop at the first file, not carry on")

    # ---- journal ----

    def test_second_run_is_a_no_op(self):
        self.scan()
        self.run_ocr()
        first = len(self.puts())
        self.assertGreater(first, 0)
        self.run_ocr()
        self.assertEqual(len(self.puts()), first,
                         "a second pass must not rewrite what is already done")

    def test_interrupted_run_resumes(self):
        self.scan()
        self.run_ocr(limit=1)
        done = len(self.puts())
        self.assertGreaterEqual(done, 1)
        self.run_ocr()
        self.assertGreater(len(self.puts()), done, "the rest must still be picked up")
        names = self.uploaded_names()
        self.assertEqual(len(names), len(set(names)), "no file may be done twice")

    # ---- audit ----

    def audit(self, **over):
        a = namespace(ref=None, sample=50, all=True, verbose=False, **over)
        buf = io.StringIO()
        code = 0
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                self.coord.cmd_audit(a)
        except SystemExit as e:
            code = e.code or 0
        return code, buf.getvalue()

    def test_audit_passes_after_a_clean_run(self):
        self.scan()
        self.run_ocr()
        code, out = self.audit()
        self.assertEqual(code, 0, out)
        self.assertIn("0 problem(s)", out)

    def test_audit_compares_against_the_real_previous_version(self):
        # The fake returns versions oldest first. Taking element [1] would
        # compare the current file with itself and pass on anything.
        self.scan()
        self.run_ocr()
        item = self.puts()[0][1]
        self.assertGreaterEqual(len(self.cloud.history[item]), 2)
        self.assertNotEqual(self.cloud.history[item][0],
                            self.cloud.history[item][-1])
        code, out = self.audit()
        self.assertEqual(code, 0, out)

    def test_audit_catches_a_missing_version_history(self):
        self.scan()
        self.run_ocr()
        for _, item, _ in self.puts():
            self.cloud.versions[item] = 1          # history quietly gone
        code, out = self.audit()
        self.assertEqual(code, 1)
        self.assertIn("NOT recoverable", out)

    def test_audit_catches_a_file_that_lost_its_text(self):
        self.scan()
        self.run_ocr()
        item = self.puts()[0][1]
        # Somebody replaced the searchable version with the raw scan again.
        self.cloud.content[item] = self.cloud.history[item][0]
        code, out = self.audit()
        self.assertEqual(code, 1)
        self.assertIn("no text gained", out)

    def test_toolchain_is_recorded_per_file(self):
        # Reproducibility: months later, "which versions produced this file?"
        # has to have an answer, and ocrmypdf alone is not one.
        self.scan()
        self.run_ocr()
        db = self.db()
        ids = [r[0] for r in db.execute(
            "select toolchain from ocr where result='ok'")]
        self.assertTrue(ids, "nothing was processed")
        self.assertTrue(all(ids), "every processed file needs a toolchain id")
        row = db.execute("select ocrmypdf, tesseract, qpdf, poppler, platform "
                         "from toolchains where id=?", (ids[0],)).fetchone()
        self.assertIsNotNone(row, "the toolchain itself must be stored")
        for field, value in zip(("ocrmypdf", "tesseract", "qpdf", "poppler",
                                 "platform"), row):
            with self.subTest(field=field):
                self.assertNotIn(value, (None, "", "?"), field + " not captured")

    def test_no_gain_is_recorded_and_not_retried(self):
        self.scan()
        self.run_ocr()
        row = self.db().execute(
            "select result from ocr where path like '%no-legible-text.pdf'").fetchone()
        self.assertEqual(row[0], "no-gain")
        self.run_ocr()
        self.assertNotIn("no-legible-text.pdf", self.uploaded_names())


if __name__ == "__main__":
    unittest.main()
