# Security notes

`CANVAS_COOKIE` is an authentication credential. Anyone who obtains an active
value may be able to access the same Canvas account until the session expires or
is revoked.

- Keep the CLI and cookie on your own Mac.
- Use the hidden `read -s` command in `README.md`.
- Never commit, upload, log, email, or paste the cookie into chat.
- Do not store it in this project folder.
- Run `unset CANVAS_COOKIE` when finished.
- Refresh or log out of the corresponding Canvas session if compromise is
  suspected.

This project deliberately performs only HTTP GET requests. It prevents the
Canvas cookie from being attached to requests for other hostnames and strips
sensitive headers on cross-origin redirects.
