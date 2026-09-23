"""A pretend Microsoft Graph, just large enough to drive the coordinator.

It models the handful of endpoints ocr-in-place actually uses, plus the three
situations that are impossible to rehearse against a live tenant without
risking real documents:

  * somebody edits a file while OCR is running   -> cTag changes
  * the library keeps no version history         -> version count never rises
  * the service is rate limiting                 -> 429 with Retry-After

Not a Graph emulator. It answers what this program asks and nothing else.
"""
import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Cloud:
    def __init__(self, drive_id="d1"):
        self.drive_id = drive_id
        self.items = {}          # item_id -> dict
        self.folders = {}        # path -> item_id
        self.content = {}        # item_id -> bytes
        self.versions = {}       # item_id -> int
        self.history = {}        # item_id -> [bytes, oldest first]
        self.uploads = {}        # token -> item_id
        self.sessions = 0
        self.log = []            # ("put", item_id, nbytes) and friends
        self.lock = threading.Lock()
        # switches the tests flip
        self.change_ctag_on_download = False
        self.freeze_versions = False
        self.throttle_metadata = 0     # 429s on a single file's metadata
        self.throttle_listing = 0      # 429s on folder listings
        self.seen_metadata = 0
        self.seen_listing = 0

    # ---- building a fixture drive ----

    def _folder(self, path):
        """Create the folder items a real drive would have, once each."""
        parts = [x for x in path.strip("/").split("/") if x]
        for i, part in enumerate(parts):
            full = "/".join(parts[:i + 1])
            if full in self.folders:
                continue
            fid = "f%d" % (len(self.folders) + 1)
            parent = "/".join(parts[:i])
            self.folders[full] = fid
            self.items[fid] = {
                "id": fid, "name": part, "size": 0,
                "folder": {"childCount": 0},
                "cTag": '"c:{%s,0}"' % fid, "eTag": '"%s,0"' % fid,
                "fileSystemInfo": {"lastModifiedDateTime": "2019-05-04T10:00:00Z"},
                "lastModifiedDateTime": "2019-05-04T10:00:00Z",
                "webUrl": "https://example.invalid/" + full,
                "parentReference": {"driveId": self.drive_id,
                                    "path": "/drive/root:/" + parent if parent
                                    else "/drive/root:"},
            }

    def add(self, path, local_file):
        item_id = "i%d" % (len(self.items) + 1)
        with open(local_file, "rb") as f:
            data = f.read()
        folder, _, name = path.rpartition("/")
        if folder:
            self._folder(folder)
        self.items[item_id] = {
            "id": item_id, "name": name, "size": len(data),
            "file": {"mimeType": "application/pdf"},
            "cTag": '"c:{%s,1}"' % item_id, "eTag": '"%s,1"' % item_id,
            "fileSystemInfo": {"lastModifiedDateTime": "2019-05-04T10:00:00Z"},
            "lastModifiedDateTime": "2019-05-04T10:00:00Z",
            "webUrl": "https://example.invalid/" + urllib.parse.quote(path),
            "parentReference": {"driveId": self.drive_id,
                                "path": "/drive/root:/" + folder if folder
                                else "/drive/root:"},
        }
        self.content[item_id] = data
        self.versions[item_id] = 1
        self.history[item_id] = [data]
        return item_id

    def bump(self, item_id, data=None):
        """Record a new version, the way SharePoint would."""
        with self.lock:
            if not self.freeze_versions:
                self.versions[item_id] += 1
            n = self.versions[item_id]
            it = self.items[item_id]
            it["cTag"] = '"c:{%s,%d}"' % (item_id, n)
            it["eTag"] = '"%s,%d"' % (item_id, n)
            if data is not None:
                self.content[item_id] = data
                self.history.setdefault(item_id, []).append(data)
                it["size"] = len(data)

    def touch_elsewhere(self, item_id):
        """Somebody else saved the file. Same shape as bump, new cTag."""
        self.bump(item_id)


def serve(cloud):
    """Start the server. Returns (base_url, shutdown)."""

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body=b"", kind="application/json", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode())

        def _throttled(self, listing=False):
            if listing:
                if cloud.throttle_listing > cloud.seen_listing:
                    cloud.seen_listing += 1
                    self._send(429, b"", extra={"Retry-After": "0"})
                    return True
                return False
            if cloud.throttle_metadata > cloud.seen_metadata:
                cloud.seen_metadata += 1
                self._send(429, b"", extra={"Retry-After": "0"})
                return True
            return False

        # -------- GET --------
        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            p = u.path

            # content download, reached through @microsoft.graph.downloadUrl
            if p.startswith("/content/"):
                item = p.split("/content/", 1)[1]
                cloud.log.append(("get", item, 0))
                data = cloud.content.get(item)
                if data is None:
                    return self._send(404)
                return self._send(200, data, "application/pdf")

            if not p.startswith("/v1.0/"):
                return self._send(404)
            rest = p[len("/v1.0"):]

            # /drives/<id>
            if rest == "/drives/" + cloud.drive_id:
                return self._json({"id": cloud.drive_id, "name": "Documents",
                                   "driveType": "documentLibrary",
                                   "webUrl": "https://example.invalid/Documents"})

            # a specific version's bytes
            if "/versions/" in rest and rest.endswith("/content"):
                item = rest.split("/items/")[1].split("/")[0]
                vid = rest.split("/versions/")[1].rsplit("/content", 1)[0]
                hist = cloud.history.get(item, [])
                idx = int(float(vid)) - 1
                if not (0 <= idx < len(hist)):
                    return self._send(404)
                return self._send(200, hist[idx], "application/pdf")

            # the current bytes
            if rest.endswith("/content") and "/items/" in rest:
                item = rest.split("/items/")[1].rsplit("/content", 1)[0]
                data = cloud.content.get(item)
                return self._send(200, data, "application/pdf") if data is not None \
                    else self._send(404)

            # /drives/<id>/items/<item>/versions
            if rest.endswith("/versions"):
                item = rest.split("/items/")[1].split("/")[0]
                n = cloud.versions.get(item, 0)
                # Deliberately oldest first, with real timestamps: a caller that
                # simply takes element [1] would compare the file with itself.
                return self._json({"value": [
                    {"id": "%d.0" % (i + 1),
                     "lastModifiedDateTime": "2019-05-%02dT10:00:00Z" % (i + 1)}
                    for i in range(n)]})

            # /drives/<id>/items/<item>
            if "/items/" in rest:
                if self._throttled():
                    return
                item = rest.split("/items/")[1]
                it = cloud.items.get(item)
                if it is None:
                    return self._send(404)
                out = dict(it)
                out["@microsoft.graph.downloadUrl"] = (
                    "http://127.0.0.1:%d/content/%s" % (self.server.server_address[1], item))
                if cloud.change_ctag_on_download:
                    # The moment we hand out a download URL, somebody else saves
                    # the file. The coordinator must notice before uploading.
                    cloud.touch_elsewhere(item)
                return self._json(out)

            # children listings
            if rest.endswith("/children"):
                if self._throttled(listing=True):
                    return
                folder = ""
                if "root:/" in rest:
                    folder = urllib.parse.unquote(
                        rest.split("root:/", 1)[1].rsplit(":/children", 1)[0])
                kids = []
                for it in cloud.items.values():
                    parent = it["parentReference"]["path"].split("root:", 1)[1].strip("/")
                    if parent == folder:
                        kids.append(it)
                return self._json({"value": kids})

            return self._send(404)

        # -------- POST --------
        def do_POST(self):
            p = urllib.parse.urlsplit(self.path).path
            if p.endswith("/createUploadSession"):
                item = p.split("/items/")[1].split("/")[0]
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                cloud.sessions += 1
                token = "s%d" % cloud.sessions
                cloud.uploads[token] = (item, body.get("item", {}))
                return self._json({"uploadUrl": "http://127.0.0.1:%d/upload/%s"
                                   % (self.server.server_address[1], token)})
            return self._send(404)

        # -------- PUT --------
        def do_PUT(self):
            p = urllib.parse.urlsplit(self.path).path
            if not p.startswith("/upload/"):
                return self._send(404)
            token = p.split("/upload/", 1)[1]
            if token not in cloud.uploads:
                return self._send(404)
            item, props = cloud.uploads[token]
            n = int(self.headers.get("Content-Length", 0))
            chunk = self.rfile.read(n)
            rng = self.headers.get("Content-Range", "")
            buf = getattr(self.server, "_buf", {})
            buf[token] = buf.get(token, b"") + chunk
            self.server._buf = buf
            total = int(rng.rsplit("/", 1)[1]) if "/" in rng else len(chunk)
            if len(buf[token]) < total:
                return self._send(202)
            cloud.log.append(("put", item, len(buf[token])))
            cloud.bump(item, buf[token])
            it = dict(cloud.items[item])
            fsi = props.get("fileSystemInfo")
            if fsi:
                it["fileSystemInfo"] = fsi
                it["lastModifiedDateTime"] = fsi.get("lastModifiedDateTime")
            del buf[token]
            return self._json(it, 201)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d/v1.0" % srv.server_address[1]

    def stop():
        srv.shutdown()
        srv.server_close()

    return base, stop
