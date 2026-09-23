# Setting up Infomaniak kDrive access

kDrive uses a simple bearer token rather than an OAuth application.

## 1. Create a token

At [manager.infomaniak.com](https://manager.infomaniak.com) → your profile →
**API tokens** (in the developer section) → create a token with the scope
**`drive`**. Creating one asks for the password of the user you are logged in
as.

The token must belong to a **user of the account that owns the drive**. This is
the one thing that reliably costs an afternoon: an Infomaniak login can be
attached to several accounts, and a token issued from the wrong one
authenticates perfectly well while returning 403 for every drive. Check with:

```bash
curl -s -H "Authorization: Bearer $TOKEN" https://api.infomaniak.com/2/profile
curl -s -H "Authorization: Bearer $TOKEN" https://api.infomaniak.com/1/account
```

## 2. Store it

```bash
sudo install -d -m 700 /etc/ocr-in-place
sudo install -m 600 /dev/null /etc/ocr-in-place/kdrive-token
# paste the token into the file, nothing else, no trailing newline needed
```

Or set `KDRIVE_TOKEN` in the environment. Prefer reading it in over stdin to
passing it on a command line, where it would land in your shell history and in
`ps` output.

## 3. Find the drive and go

```bash
ocr-in-place-kdrive drives
ocr-in-place-kdrive --drive 1234567 ls
ocr-in-place-kdrive --drive 1234567 scan "Common documents" -r
ocr-in-place-kdrive --drive 1234567 run  "Common documents" -r --lang deu+eng --yes
```

## How the in-place replacement works here

```
POST /3/drive/<drive>/upload?file_id=<id>&total_size=<bytes>
```

Uploading against an existing `file_id` replaces the contents and moves the old
contents into the version history. Same shape as the SharePoint approach: same
ID, same path, new version. Restore with:

```
POST /2/drive/<drive>/files/<id>/versions/<version>/restore
```

## Two traps

- **`total_size=0` does not fail — it empties the file.** The API accepts it
  happily and you are left with a zero-byte document and a version history to
  climb back out of. `ocr-in-place-kdrive` refuses to upload an empty body for
  exactly this reason. Keep that check if you adapt the code.
- **There is no pre-authorised URL.** Unlike Graph, kDrive has nothing that can
  be handed to an untrusted worker, so this variant does all the work on one
  machine. That is why it is a separate, simpler program rather than a backend
  of the main one.

## A note on search

kDrive's own search matches **file names**. A word that exists only inside the
document is not found, with or without a text layer. That is not a failing of
kDrive so much as a reminder of what the text layer is actually for: the
searchability travels *inside the file*, so it keeps working in Acrobat, in
Spotlight, in the next system you migrate to, and for any program that opens
the document — including the AI assistant you point at your archive.
