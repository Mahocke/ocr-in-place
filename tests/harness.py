"""Shared helpers for the test suite."""
import importlib.machinery
import importlib.util
import re
import subprocess


def load(path, name):
    """Import one of the extensionless scripts in bin/ as a module."""
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def page_count(path):
    out = subprocess.run(["pdfinfo", path], capture_output=True,
                         text=True).stdout
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(m.group(1)) if m else 0


def text_page_count(path, min_chars=50):
    out = subprocess.run(["pdftotext", "-q", path, "-"],
                         capture_output=True).stdout.decode("utf-8", "replace")
    pages = out.split("\f")[:-1] or [out]
    return sum(1 for p in pages if len(re.sub(r"\s", "", p)) >= min_chars)
