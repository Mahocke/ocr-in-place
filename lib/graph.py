"""Minimal Microsoft Graph client for ocr-in-place.

App-only authentication (client credentials) against a registered Entra
application. Standard library only - no third-party packages.

Credentials are read, in this order:
  1. environment: OCR_TENANT, OCR_CLIENT_ID, OCR_CLIENT_SECRET
  2. an INI file, default /etc/ocr-in-place/config.ini, overridable with
     OCR_CONFIG:

        [graph]
        tenant        = contoso.onmicrosoft.com
        client_id     = 00000000-0000-0000-0000-000000000000
        client_secret = ...

The file holds a secret that can read and write every document in the
tenant. Keep it at mode 600 and run the tool as its owner.
"""
import configparser
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.microsoft.com/v1.0"
CONFIG = os.environ.get("OCR_CONFIG", "/etc/ocr-in-place/config.ini")
STATE = os.environ.get("OCR_STATE", "/var/lib/ocr-in-place")
RUN = os.environ.get("OCR_RUN", "/run/ocr-in-place")
TOKEN_CACHE = os.path.join(RUN, "graph-token.json")
SITES_CACHE = os.path.join(STATE, "sites.json")
SITES_TTL = 6 * 3600
CHUNK = 10 * 1024 * 1024  # upload session: must be a multiple of 320 KiB

_ctx = ssl.create_default_context()


class GraphError(Exception):
    pass


def die(msg, code=1):
    print("error: " + str(msg), file=sys.stderr)
    sys.exit(code)


# ---------- authentication ----------

def _credentials():
    env = (os.environ.get("OCR_TENANT"), os.environ.get("OCR_CLIENT_ID"),
           os.environ.get("OCR_CLIENT_SECRET"))
    if all(env):
        return env
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG)
    if "graph" not in cfg:
        die(f"no credentials: set OCR_TENANT/OCR_CLIENT_ID/OCR_CLIENT_SECRET "
            f"or create {CONFIG} with a [graph] section (see docs/SETUP-SHAREPOINT.md)")
    s = cfg["graph"]
    try:
        return s["tenant"], s["client_id"], s["client_secret"]
    except KeyError as e:
        die(f"{CONFIG}: [graph] is missing {e}")


def token():
    """Application token, cached in RAM (/run) until shortly before it expires."""
    try:
        with open(TOKEN_CACHE) as f:
            c = json.load(f)
        if c["exp"] - 120 > time.time():
            return c["tok"]
    except Exception:
        pass
    tenant, cid, secret = _credentials()
    body = urllib.parse.urlencode({
        "client_id": cid, "client_secret": secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ctx) as r:
            j = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise GraphError(f"token request: HTTP {e.code}: "
                         f"{e.read()[:300].decode('utf-8', 'replace')}")
    try:
        os.makedirs(os.path.dirname(TOKEN_CACHE), mode=0o700, exist_ok=True)
        fd = os.open(TOKEN_CACHE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"tok": j["access_token"],
                       "exp": time.time() + int(j["expires_in"])}, f)
    except OSError:
        pass  # cache is an optimisation, not a requirement
    return j["access_token"]


# ---------- requests ----------

def call(method, path, *, params=None, json_body=None, data=None, headers=None,
         retries=4, timeout=120):
    """One Graph call, retrying on 429 and 5xx. `path` is relative to /v1.0,
    or an absolute URL (used when following @odata.nextLink)."""
    url = path if path.startswith("http") else GRAPH + path
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    body = data
    h = {"Authorization": "Bearer " + token()}
    if json_body is not None:
        body = json.dumps(json_body).encode()
        h["Content-Type"] = "application/json"
    if headers:
        h.update(headers)
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt < retries:
                wait = 0
                try:
                    wait = int(e.headers.get("Retry-After") or 0)
                except Exception:
                    pass
                time.sleep(min(120, wait or 2 ** attempt))
                continue
            raw = e.read()[:400].decode("utf-8", "replace")
            try:
                msg = json.loads(raw)["error"]["message"]
            except Exception:
                msg = raw
            raise GraphError(f"{method} {path} -> HTTP {e.code}: {msg}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise GraphError(f"{method} {path}: {e}")
    raise GraphError(f"{method} {path}: gave up after {retries} attempts")


def get(path, **kw):
    raw = call("GET", path, **kw)
    return json.loads(raw) if raw else {}


def post(path, **kw):
    raw = call("POST", path, **kw)
    return json.loads(raw) if raw else {}


def pages(path, params=None):
    """Every item of a paged collection, following @odata.nextLink."""
    url, p = path, params
    while url:
        j = get(url, params=p)
        yield from j.get("value", [])
        url, p = j.get("@odata.nextLink"), None


# ---------- addressing ----------

def enc_path(p):
    """Encode a path for /root:/<path>: - including spaces, # and %."""
    return "/".join(urllib.parse.quote(s, safe="")
                    for s in p.strip("/").split("/") if s)


def item_url(drive_id, path):
    p = enc_path(path)
    return f"/drives/{drive_id}/root:/{p}" if p else f"/drives/{drive_id}/root"


def item_sub(drive_id, path, sub):
    """.../root:/<path>:/<sub> or .../root/<sub> at the drive root."""
    p = enc_path(path)
    return (f"/drives/{drive_id}/root:/{p}:/{sub}" if p
            else f"/drives/{drive_id}/root/{sub}")


def item_path(it):
    """Readable path from parentReference.path (.../root:/a/b) plus the name."""
    p = (it.get("parentReference") or {}).get("path", "")
    p = urllib.parse.unquote(p.split("root:", 1)[1]) if "root:" in p else ""
    return (p.rstrip("/") + "/" + it["name"]).lstrip("/")


# ---------- sites and drives ----------

def all_sites(refresh=False):
    try:
        if not refresh and time.time() - os.path.getmtime(SITES_CACHE) < SITES_TTL:
            with open(SITES_CACHE) as f:
                return json.load(f)
    except Exception:
        pass
    sites = [{"id": s["id"], "name": s.get("name", ""),
              "displayName": s.get("displayName", ""), "webUrl": s.get("webUrl", "")}
             for s in pages("/sites/getAllSites")]
    try:
        os.makedirs(os.path.dirname(SITES_CACHE), exist_ok=True)
        with open(SITES_CACHE, "w") as f:
            json.dump(sites, f)
    except OSError:
        pass
    return sites


def find_site(name):
    n = name.lower()
    for refresh in (False, True):
        # Team sites before personal OneDrives: display names collide.
        for s in sorted(all_sites(refresh=refresh),
                        key=lambda s: "-my.sharepoint.com" in s["webUrl"]):
            last = s["webUrl"].rstrip("/").split("/")[-1].lower()
            if n in (s["name"].lower(), s["displayName"].lower(), last):
                return s
    die(f"site '{name}' not found")


def site_drives(site_id):
    return list(pages(f"/sites/{site_id}/drives"))


def find_drive(site, lib):
    if not lib:
        return get(f"/sites/{site['id']}/drive")
    want = urllib.parse.unquote(lib).lower()
    for d in site_drives(site["id"]):
        seg = urllib.parse.unquote(d["webUrl"].rstrip("/").split("/")[-1]).lower()
        if want in (d["name"].lower(), seg):
            return d
    die(f"document library '{lib}' not found in site {site['name']}")


def resolve(ref):
    """REF -> (drive, path). See README for the accepted spellings."""
    ref = ref.strip()
    if ref.startswith("http://") or ref.startswith("https://"):
        u = urllib.parse.urlsplit(ref)
        full = urllib.parse.unquote(u.path).rstrip("/")
        best = None
        for s in all_sites():
            sp = urllib.parse.unquote(
                urllib.parse.urlsplit(s["webUrl"]).path).rstrip("/")
            if (urllib.parse.urlsplit(s["webUrl"]).netloc == u.netloc
                    and (full == sp or full.startswith(sp + "/"))):
                if best is None or len(sp) > len(best[1]):
                    best = (s, sp)
        if not best:
            die(f"no site matches the URL {ref}")
        site, sp = best
        rest = full[len(sp):].strip("/").split("/", 1)
        lib = rest[0] if rest and rest[0] else ""
        path = rest[1] if len(rest) > 1 else ""
        return find_drive(site, lib), path
    if ref.startswith("drive:"):
        rest = ref[6:].split("/", 1)
        return get(f"/drives/{rest[0]}"), (rest[1] if len(rest) > 1 else "")
    head, _, path = ref.partition("/")
    if "@" in head:
        return get(f"/users/{head}/drive"), path
    name, _, lib = head.partition(":")
    return find_drive(find_site(name), lib), path
