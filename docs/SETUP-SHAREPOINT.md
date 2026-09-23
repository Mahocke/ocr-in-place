# Setting up SharePoint / OneDrive access

The tool authenticates as an **application**, not as a user. No interactive
login, no refresh tokens, nothing to renew every 90 days — and it can reach
documents that belong to people who are on holiday.

That also means the credential is powerful. Treat it accordingly.

## 1. Register an application

In the Entra admin center → **App registrations** → *New registration*:

- name: anything, e.g. `ocr-in-place`
- supported account types: *Accounts in this organizational directory only*
- redirect URI: none

Note the **Application (client) ID** and the **Directory (tenant) ID**.

## 2. Grant the permission

Under **API permissions** → *Add a permission* → *Microsoft Graph* →
**Application permissions**:

| Permission | Why |
|---|---|
| `Sites.ReadWrite.All` | read and write documents in SharePoint and OneDrive |

Then click **Grant admin consent**. Without that step every call comes back
403.

One application permission on the whole tenant is a blunt instrument. If that
is more than you want to hand out, Microsoft's
[Sites.Selected](https://learn.microsoft.com/graph/permissions-reference)
permission lets an administrator grant write access to individual sites only.
The tool works with it unchanged, as long as every site you point it at has
been granted.

## 3. Create a secret

Under **Certificates & secrets** → *New client secret*. Copy the **value**
immediately; it is never shown again. Set an expiry you will remember — when it
lapses, the tool stops with an HTTP 401 from the token endpoint.

## 4. Store the credentials

```bash
sudo install -d -m 700 /etc/ocr-in-place
sudo install -m 600 /dev/null /etc/ocr-in-place/config.ini
sudo editor /etc/ocr-in-place/config.ini
```

```ini
[graph]
tenant        = contoso.onmicrosoft.com
client_id     = 00000000-0000-0000-0000-000000000000
client_secret = ...
```

Or pass `OCR_TENANT`, `OCR_CLIENT_ID` and `OCR_CLIENT_SECRET` in the
environment, which is usually the nicer option in a container.

The token itself is cached in `/run/ocr-in-place/graph-token.json` at mode 600,
on a tmpfs, so it does not survive a reboot.

## 5. Check it

```bash
ocr-in-place scan contoso
```

`scan` only reads. If it lists PDFs with a classification next to them,
everything is in place.

## Addressing files

`REF` accepts any of these:

```
contoso                                     site, default library, root
contoso/Finance/2024                        site, default library, path
contoso:Shared Documents/Finance            site:library/path
user@example.com/Documents/Scans            somebody's personal OneDrive
drive:b!xxxxxxxx/Finance                    drive ID, then path
https://contoso.sharepoint.com/sites/...    the URL from the browser
```

Site names are matched case-insensitively against the name, the display name
and the last segment of the site URL. The list of sites is cached for six
hours.

## Things worth knowing

- **Throttling is normal.** SharePoint answers 429 under load. The tool honours
  `Retry-After` and never stores a throttled file as broken. Around eight
  concurrent jobs and six walk threads is a comfortable ceiling; twelve
  produced hundreds of 429s.
- **Some drives are locked.** A few OneDrives answer HTTP 423 at the drive
  level (retention or legal hold). Skip them; there is nothing to do from the
  outside.
- **The version history has to exist.** It is on by default in modern document
  libraries, but check before a large run. The version counter net (see
  [HOW-IT-WORKS.md](HOW-IT-WORKS.md)) catches it on the first file, which is
  one file too late for comfort but not a disaster.
