# Security policy

## Secrets

This plugin reads **one** secret: `BILI_COOKIE`, from the plugin-root `.env` file.
The `.env` file is git-ignored and must never be committed, attached to an issue,
or pasted into a log.

The runtime never prints cookie values:

- Python logs only the cookie item count and whether `SESSDATA` / `buvid3` exist.
- Host-side errors only include the Bilibili message and a short stderr tail.
- Tool results contain public Bilibili data only.

If you accidentally publish a cookie, revoke it in your Bilibili account settings
and replace it with a new one.

## Reporting

Open a private security advisory on the repository, or contact the maintainer
through the repository profile. Please do not file a public issue with a cookie
or another secret in it.

## Scope

The plugin only calls Bilibili public web APIs. It does not collect analytics,
does not phone home, and does not write outside the plugin directory.
