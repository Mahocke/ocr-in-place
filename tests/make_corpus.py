#!/usr/bin/env python3
"""Build a corpus of awkward PDFs for the test suite.

Everything here is generated, not checked in, so the corpus stays a few lines
of code instead of a directory of opaque binaries. Run it once:

    python3 tests/make_corpus.py [outdir]

Needs ocrmypdf, poppler-utils, qpdf and (for the JPEG cases) sips or any tool
that writes a JPEG. Files it cannot build are reported and skipped rather than
failing the whole run - a missing case makes its tests skip, not lie.
"""
import os
import random
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "corpus")


# ---------- a very small PDF writer ----------

def build_pdf(objects, root=1):
    """objects: list of str or bytes bodies, 1-indexed in order."""
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objects, 1):
        if isinstance(body, str):
            body = body.encode("latin-1")
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for o in offsets:
        out += b"%010d 00000 n \n" % o
    out += (b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, root, start))
    return out


def text_page_pdf(pages_text):
    """One text object per page, Helvetica, deterministic."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, None]
    kids, n = [], 3
    contents, pages = [], []
    for lines in pages_text:
        stream = ("BT /F1 22 Tf 60 760 Td 30 TL\n"
                  + "".join("(%s) Tj T*\n" % l for l in lines) + "ET")
        contents.append(stream)
    font_obj = 3 + 2 * len(pages_text)
    for i, stream in enumerate(contents):
        page_no = 3 + 2 * i
        cont_no = page_no + 1
        pages.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                     "/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                     % (font_obj, cont_no))
        kids.append("%d 0 R" % page_no)
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join(kids), len(kids))]
    for page, stream in zip(pages, contents):
        objs.append(page)
        objs.append("<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    return build_pdf(objs)


def put(path, data):
    with open(path, "wb") as f:
        f.write(data)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, **kw)


def rasterise(src, dst, dpi=150, fmt="png"):
    """Render a PDF to an image and wrap it back into an image-only PDF.

    --tesseract-timeout 0 makes ocrmypdf skip OCR entirely, so what comes out
    is a picture of the page with no text layer at all - exactly what a scanner
    produces."""
    base = dst + ".page"
    run(["pdftoppm", "-r", str(dpi), "-" + fmt, "-singlefile", src, base])
    img = base + "." + fmt
    if not os.path.exists(img):
        return None
    r = run(["ocrmypdf", "--image-dpi", str(dpi), "--tesseract-timeout", "0",
             "--output-type", "pdf", "--quiet", img, dst])
    os.remove(img)
    return dst if r.returncode == 0 and os.path.exists(dst) else None


# ---------- the cases ----------

# Every generated text page must clear MIN_CHARS (50 non-whitespace characters)
# by a comfortable margin. A page below the threshold counts as having no text,
# which is correct behaviour but makes for a misleading fixture.
INVOICE = ["INVOICE 2024-0815", "Acme Corporation Ltd", "Bahnhofstrasse 14, 8001 Zurich",
           "Amount due: 1,234.56 EUR", "Payment terms: 30 days net",
           "Reference: procedures applicable 4711"]
LETTER = ["Page two of the same document", "Delivery address: Musterweg 3",
          "All prices are net of value added tax", "Our reference: AC-2024-0815",
          "Please quote this number on payment", "Thank you for your business"]


def case_full_text(p):
    """Every page already carries text. Must be classified 'text' and skipped."""
    put(p, text_page_pdf([INVOICE, LETTER]))
    return p


def case_image_scan(p):
    """A pure scan: one page, no text layer, but legible words on it."""
    src = p + ".src"
    put(src, text_page_pdf([INVOICE]))
    got = rasterise(src, p)
    os.remove(src)
    return got


def case_partial_text(p):
    """Page 1 has text, page 2 is a scan. The interesting middle case."""
    scan = p + ".scan"
    if not case_image_scan(scan):
        return None
    txt = p + ".txt.pdf"
    put(txt, text_page_pdf([LETTER]))
    r = run(["qpdf", "--empty", "--pages", txt, "1", scan, "1", "--", p])
    for f in (scan, txt):
        os.path.exists(f) and os.remove(f)
    return p if r.returncode == 0 and os.path.exists(p) else None


def case_no_legible_text(p):
    """A scan of pure geometry - OCR will find nothing worth uploading."""
    random.seed(7)
    parts = []
    for _ in range(60):
        x, y = random.randint(40, 520), random.randint(40, 780)
        parts.append("%d %d %d %d re f" % (x, y, random.randint(4, 30),
                                           random.randint(4, 30)))
    stream = "0.2 0.2 0.2 rg\n" + "\n".join(parts)
    src = p + ".src"
    put(src, build_pdf([
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R >>",
        "<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)]))
    got = rasterise(src, p)
    os.remove(src)
    return got


def case_zero_pages(p):
    """A structurally valid PDF with no pages at all."""
    put(p, build_pdf([
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [] /Count 0 >>"]))
    return p


def case_not_a_pdf(p):
    """Random bytes behind a PDF header - the classic mislabelled file."""
    random.seed(11)
    put(p, b"%PDF-1.4\n" + bytes(random.randrange(256) for _ in range(4000)))
    return p


def case_broken_xref(p):
    """Valid objects, wrecked cross-reference table. Poppler usually repairs
    this silently; the point is that we must not treat 'repaired' as 'fine to
    rewrite' without the usual checks."""
    raw = text_page_pdf([INVOICE])
    i = raw.index(b"xref")
    head, tail = raw[:i], raw[i:]
    tail = tail.replace(b"00000 n", b"99999 n")
    put(p, head + tail)
    return p


def case_truncated(p):
    """Cut off mid-file: no xref, no trailer, no EOF."""
    raw = text_page_pdf([INVOICE])
    put(p, raw[:int(len(raw) * 0.55)])
    return p


def case_encrypted(p):
    """Password protected. Must never be silently rewritten."""
    src = p + ".src"
    put(src, text_page_pdf([["Confidential - do not touch"] + LETTER]))
    r = run(["qpdf", "--encrypt", "secret", "secret", "256", "--", src, p])
    os.remove(src)
    return p if r.returncode == 0 and os.path.exists(p) else None


def case_signed(p):
    """A PDF carrying a signature field. The signature is a placeholder, which
    is fine: the guard greps pdfsig for a signature being present at all, and
    a document whose signature does not even validate is the last thing we
    want to rewrite."""
    contents = "<" + "00" * 512 + ">"
    sig = ("<< /Type /Sig /Filter /Adobe.PPKLite /SubFilter /adbe.pkcs7.detached "
           "/ByteRange [0 1000 2000 1000] /Contents " + contents +
           " /M (D:20240101120000Z) /Name (Test Signer) >>")
    stream = "BT /F1 22 Tf 60 760 Td (Signed document) Tj ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [5 0 R] /SigFlags 3 >> >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Annots [5 0 R] "
        "/Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R >>",
        "<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        "<< /Type /Annot /Subtype /Widget /FT /Sig /T (Signature1) /Rect [0 0 0 0] "
        "/V 6 0 R /P 3 0 R /F 132 >>",
        sig,
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    put(p, build_pdf(objs))
    return p


def case_damaged_jpeg(p):
    """A scan whose JPEG data is corrupted. This is the file that behaves
    differently on qpdf 11 and qpdf 12, and the reason the PDF/A fallback
    exists at all."""
    src = p + ".src"
    put(src, text_page_pdf([["DAMAGED SCAN"] + INVOICE]))
    base = p + ".page"
    run(["pdftoppm", "-r", "150", "-jpeg", "-singlefile", src, base])
    os.remove(src)
    jpg = base + ".jpg"
    if not os.path.exists(jpg):
        return None
    clean = p + ".clean"
    r = run(["ocrmypdf", "--image-dpi", "150", "--tesseract-timeout", "0",
             "--output-type", "pdf", "--quiet", jpg, clean])
    os.remove(jpg)
    if r.returncode != 0 or not os.path.exists(clean):
        return None
    with open(clean, "rb") as f:
        raw = bytearray(f.read())
    os.remove(clean)
    # Corrupt entropy-coded data well inside the JPEG stream: the PDF stays
    # structurally sound, the image does not.
    i = raw.find(b"\xff\xda")            # start of scan marker
    if i < 0:
        return None
    random.seed(23)
    for off in range(i + 400, min(i + 2400, len(raw) - 2)):
        raw[off] = random.randrange(256)
    put(p, bytes(raw))
    return p


CASES = [
    ("full-text.pdf", case_full_text),
    ("image-scan.pdf", case_image_scan),
    ("partial-text.pdf", case_partial_text),
    ("no-legible-text.pdf", case_no_legible_text),
    ("zero-pages.pdf", case_zero_pages),
    ("not-a-pdf.pdf", case_not_a_pdf),
    ("broken-xref.pdf", case_broken_xref),
    ("truncated.pdf", case_truncated),
    ("encrypted.pdf", case_encrypted),
    ("signed.pdf", case_signed),
    ("damaged-jpeg.pdf", case_damaged_jpeg),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    missing = [t for t in ("ocrmypdf", "pdftoppm", "qpdf") if shutil.which(t) is None]
    if missing:
        print("missing tools: " + ", ".join(missing), file=sys.stderr)
    built, failed = [], []
    for name, fn in CASES:
        path = os.path.join(OUT, name)
        try:
            got = fn(path)
        except Exception as e:
            got, err = None, str(e)[:80]
        else:
            err = ""
        for junk in (path + ".src", path + ".page.jpg", path + ".clean"):
            os.path.exists(junk) and os.remove(junk)
        if got and os.path.exists(path) and os.path.getsize(path):
            built.append(name)
            print("  built   %-22s %7d bytes" % (name, os.path.getsize(path)))
        else:
            failed.append(name)
            print("  SKIPPED %-22s %s" % (name, err or "could not be generated"))
    print("\n%d built, %d skipped -> %s" % (len(built), len(failed), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
