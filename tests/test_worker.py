#!/usr/bin/env python3
"""Worker tests against the awkward-PDF corpus.

These assert *safety invariants*, not success. Whether a damaged file can be
repaired depends on the versions of ocrmypdf, qpdf and Ghostscript on the
machine; whether it is ever silently replaced by something worse must not.

    python3 tests/make_corpus.py
    python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
CORPUS = os.path.join(HERE, "corpus")
WORKER = os.path.join(ROOT, "bin", "ocr-in-place-worker")
sys.path.insert(0, HERE)
from harness import load, page_count, text_page_count  # noqa: E402

w = load(WORKER, "worker")


class Store(BaseHTTPRequestHandler):
    """Serves one file and swallows an upload, standing in for the cloud."""
    serve = b""
    received = None
    fail_times = 0          # answer this many requests with 429 first
    seen = 0

    def do_GET(self):
        cls = type(self)
        if cls.seen < cls.fail_times:
            cls.seen += 1
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(cls.serve)))
        self.end_headers()
        self.wfile.write(cls.serve)

    def do_PUT(self):
        cls = type(self)
        n = int(self.headers.get("Content-Length", 0))
        chunk = self.rfile.read(n)
        cls.received = (cls.received or b"") + chunk
        body = json.dumps({"cTag": '"c:{AFTER}"'}).encode()
        self.send_response(201)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class WorkerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(CORPUS) or not os.listdir(CORPUS):
            raise unittest.SkipTest("no corpus - run tests/make_corpus.py first")
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Store)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.port}/f.pdf"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        Store.received = None
        Store.fail_times = 0
        Store.seen = 0
        self.p = subprocess.Popen([WORKER], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, bufsize=1)

    def tearDown(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=30)
        except Exception:
            self.p.kill()

    # ---- protocol helper ----

    def send(self, job, on_ready=None):
        self.p.stdin.write(json.dumps(job) + "\n")
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            self.assertTrue(line, "worker died")
            res = json.loads(line)
            if res.get("phase") != "ready":
                return res
            answer = on_ready(res) if on_ready else {"reason": "test"}
            self.p.stdin.write(json.dumps(answer) + "\n")
            self.p.stdin.flush()

    def serve(self, name):
        with open(os.path.join(CORPUS, name), "rb") as f:
            Store.serve = f.read()
        return self.url

    def ocr(self, name, accept=True):
        url = self.serve(name)
        cb = (lambda r: {"upload": url}) if accept else (lambda r: {"reason": "changed"})
        return self.send({"id": name, "mode": "ocr", "download": url,
                          "twophase": True, "lang": "eng", "jobs": 2}, cb)

    def assertNotUploaded(self, res, why):
        self.assertFalse(res.get("uploaded"), why)
        self.assertIsNone(Store.received, why + " (bytes reached the server)")

    # ---- the guards ----

    def test_probe_reports_tools(self):
        res = self.send({"id": "p", "mode": "probe"})
        self.assertTrue(res["ok"], res.get("error"))
        self.assertTrue(res["ocrmypdf"])

    def test_signed_is_never_touched(self):
        res = self.ocr("signed.pdf")
        self.assertEqual(res["status"], "signed")
        self.assertNotUploaded(res, "a signed PDF must never be rewritten")

    def test_already_searchable_is_left_alone(self):
        res = self.ocr("full-text.pdf")
        self.assertEqual(res["status"], "text")
        self.assertNotUploaded(res, "a file that already has text needs no new version")

    def test_repaired_xref_counts_as_searchable(self):
        # Poppler silently repairs the table. The file has a text layer, so it
        # must not be re-OCR'd just because its structure was damaged.
        res = self.ocr("broken-xref.pdf")
        self.assertEqual(res["status"], "text")
        self.assertNotUploaded(res, "repaired xref is not a reason to rewrite")

    def test_unreadable_files_are_not_touched(self):
        for name in ("zero-pages.pdf", "not-a-pdf.pdf", "truncated.pdf",
                     "encrypted.pdf"):
            with self.subTest(file=name):
                Store.received = None
                res = self.ocr(name)
                self.assertEqual(res["status"], "broken")
                self.assertNotUploaded(res, f"{name} must not be rewritten")

    def test_image_scan_gains_text(self):
        res = self.ocr("image-scan.pdf")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["status"], "image")
        self.assertTrue(res["uploaded"])
        self.assertGreater(res["textpages_after"], res["textpages"])
        self.assertEqual(page_count_bytes(Store.received), res["pages"])

    def test_partial_text_keeps_its_pages(self):
        res = self.ocr("partial-text.pdf")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["status"], "partial")
        self.assertEqual(res["pages"], 2)
        self.assertEqual(res["textpages"], 1)
        self.assertEqual(res["textpages_after"], 2)
        self.assertEqual(page_count_bytes(Store.received), 2)

    def test_nothing_legible_produces_no_version(self):
        res = self.ocr("no-legible-text.pdf")
        self.assertEqual(res.get("result"), "no-gain")
        self.assertNotUploaded(res, "an empty text layer is not worth a version")

    def test_refusal_stops_the_upload(self):
        # The coordinator saw a changed cTag while OCR was running.
        res = self.ocr("image-scan.pdf", accept=False)
        self.assertEqual(res.get("result"), "aborted")
        self.assertNotUploaded(res, "a refused job must upload nothing")

    def test_damaged_jpeg_either_repairs_or_fails_cleanly(self):
        # Outcome depends on the qpdf and ocrmypdf versions present. Both
        # outcomes are acceptable; a silent bad upload is not.
        res = self.ocr("damaged-jpeg.pdf")
        if res.get("uploaded"):
            self.assertEqual(page_count_bytes(Store.received), res["pages"])
            self.assertGreater(res["textpages_after"], res["textpages"])
        else:
            self.assertFalse(res.get("ok") and res.get("result") not in
                             ("no-gain", "aborted"),
                             "failure must be reported, not swallowed")
            self.assertIsNone(Store.received)

    def test_throttling_is_transient_not_broken(self):
        # A 429 must never be recorded as a defect - the coordinator would
        # skip the file silently on every later run.
        Store.fail_times = 99
        self.serve("image-scan.pdf")
        res = self.send({"id": "t", "mode": "scan", "download": self.url})
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("transient"), "429 must be flagged transient")

    def test_throttling_recovers(self):
        Store.fail_times = 2
        self.serve("image-scan.pdf")
        res = self.send({"id": "t", "mode": "scan", "download": self.url})
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["status"], "image")

    def test_scan_mode_never_uploads(self):
        for name in sorted(os.listdir(CORPUS)):
            with self.subTest(file=name):
                Store.received = None
                url = self.serve(name)
                res = self.send({"id": name, "mode": "scan", "download": url})
                self.assertFalse(res.get("uploaded"))
                self.assertIsNone(Store.received, "scan must be read-only")


def page_count_bytes(data):
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(data or b"")
        p = f.name
    try:
        return page_count(p)
    finally:
        os.remove(p)


if __name__ == "__main__":
    unittest.main()
