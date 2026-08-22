# Fulton Canvas CLI

A local, read-only downloader for weekly module materials from the three Fulton
County Schools Canvas courses configured for this project:

| Subject | Canvas course ID | Output folder |
| --- | ---: | --- |
| Math | `547327` | `Maths` |
| Science | `544570` | `Science` |
| Social Studies | `546905` | `SocialStudy` |

The CLI authenticates **only with an existing browser-session cookie**. It does
not use a Canvas API token or OAuth. It performs GET requests only.

## Requirements

- macOS
- Python 3.9 or later (`python3 --version`)
- Chrome already signed in to `fultonschools.instructure.com`

No third-party Python packages are required.

## 1. Obtain the Canvas session cookie

### Option A: Copy `canvas_session` from Application storage

If you are using **Developer Tools → Application → Storage → Cookies**:

1. Select `https://fultonschools.instructure.com` under **Cookies**.
2. Find the row whose **Name** is `canvas_session`.
3. Copy the long text from that row's **Value** column.
4. At the hidden Terminal prompt in the next section, enter the cookie in this
   exact format:

   ```text
   canvas_session=VALUE_YOU_COPIED
   ```

The `canvas_session=` part is required. Copying only the long Value causes this
error:

```text
Authentication error: CANVAS_COOKIE must contain the complete Cookie header value (name=value; ...).
```

Use the cookie's exact **Name** as shown by Chrome. If Chrome shows a different
session-cookie name, use that name instead of `canvas_session`.

### Option B: Copy the complete Cookie request header

If `canvas_session=VALUE` is accepted by the CLI but Canvas redirects back to
sign-in, the session may require additional cookies. Copy the complete Cookie
request header instead:

1. In Chrome, open the Math modules page while signed in.
2. Open Developer Tools with **Option + Command + I**.
3. Select **Network**, then reload the Canvas page.
4. Select a request sent to `fultonschools.instructure.com`.
5. Under **Request Headers**, locate `Cookie` and copy everything after
   `Cookie:`. It should resemble:

   ```text
   canvas_session=LONG_VALUE; another_cookie=ANOTHER_VALUE
   ```

   Chrome may require enabling “show provisional headers” or viewing the raw
   headers.

Treat this value like a temporary password. Do not paste it into ChatGPT, email,
source code, screenshots, shell commands, Git, or the `.env` files of other
projects.

## 2. Load the cookie without putting it in shell history

Open Terminal, change to the unzipped folder, and use a hidden prompt:

```bash
cd ~/Downloads/fulton-canvas-cli
read -s "CANVAS_COOKIE?Paste the Canvas Cookie header, then press Return: "
echo
export CANVAS_COOKIE
```

Your typing will not appear. The value exists only in that Terminal session and
is inherited by commands launched from it. Close the Terminal window when done.

If you copied the Value from the `canvas_session` row in Application storage,
type `canvas_session=` first and then paste that Value. The hidden input should
therefore contain:

```text
canvas_session=VALUE_YOU_COPIED
```

Do **not** run `export CANVAS_COOKIE='actual value'`; that can leave the cookie
in shell history.

## 3. Verify the session

```bash
./canvasctl check
```

Expected output:

```text
Canvas session is valid.
User: ...
```

If Canvas redirects to sign-in, refresh the Canvas page in Chrome and copy the
new complete Cookie header.

## 4. Inspect available modules

```bash
./canvasctl list-modules --subject all
```

You can also use `math`, `science`, or `social`.

## 5. Preview the download

By default, `--latest 1` selects the published module with the highest Canvas
position in each course. Verify that assumption with a dry run:

```bash
./canvasctl download --subject all --latest 1 --dry-run
```

If the teachers name modules by week, title matching is safer:

```bash
./canvasctl download --subject all --module-query "August 17" --dry-run
```

The match is case-insensitive and can be any unique part of the title.

## 6. Download

Latest positioned module from every course:

```bash
./canvasctl download --subject all --latest 1
```

A specific week:

```bash
./canvasctl download --subject all --module-query "August 17"
```

Two latest modules from Science:

```bash
./canvasctl download --subject science --latest 2
```

All published modules from Math:

```bash
./canvasctl download --subject math --all-modules
```

Choose another output folder:

```bash
./canvasctl download --subject all --latest 1 --output "$HOME/Desktop/Weekly Notes"
```

The default output is:

```text
~/Downloads/Grade7-Canvas/
├── Maths/
├── Science/
├── SocialStudy/
└── canvas-download-manifest.json
```

The manifest records filenames, sizes, SHA-256 hashes, module IDs, and source
item names. It never records cookies or signed download URLs.

## Security behavior

- The cookie can be supplied only through an environment variable—not a CLI
  flag—so it is not placed in command history or process arguments.
- The cookie is attached only to requests whose hostname exactly matches the
  configured Canvas hostname.
- Cross-domain redirects have Cookie, Authorization, Referer, and
  X-Requested-With headers removed.
- Cookie values and authenticated download URLs are never logged or saved.
- The program uses GET requests only and cannot submit assignments, edit
  courses, or change Canvas data.

## Current discovery coverage

The CLI downloads files found directly in module items and file links found in:

- Canvas `File` module items
- Canvas `Page` bodies
- Assignment attachments and descriptions
- Direct file-type `ExternalUrl` module items

Some embedded third-party tools, Google Drive documents, or files hidden behind
interactive external applications may not be downloadable through the Canvas
session.

## Run offline tests

Tests use fake responses and do not contact Canvas:

```bash
python3 -m unittest discover -s tests -v
```

## Remove the cookie from the current Terminal

```bash
unset CANVAS_COOKIE
```

Logging out of Canvas in Chrome should also invalidate the session eventually.
