# Contributing

Thanks for considering a contribution.

## Development

1. Fork the repository and create a branch.
2. Install the runtime dependencies:

   ```bash
   python -m pip install -r scripts/requirements.txt
   ```

3. Make the change. Keep the split: the Node host registers tools and spawns Python;
   all HTTP belongs in `scripts/bili.py`.
4. Run the syntax checks before opening a PR:

   ```bash
   node --check index.mjs
   python -m py_compile scripts/bili.py
   ```

5. For a manual check, create `.env` from `.env.example` and run:

   ```bash
   dsh --profile headless --patch ./cordis.yml "调用 bili_user_stat 查询 uid 2 的粉丝数"
   ```

## Rules

- **Never commit `.env` or a real cookie.** Use `.env.example` with placeholders.
- Keep Python 3.10 compatibility. Do not use `tomllib`, `datetime.UTC`, `typing.Self`,
  exception groups, or other 3.11+ features.
- Keep `stdout` machine-readable: the Python layer prints exactly one JSON line; logs go to stderr.
- Do not hard-code cookies, user ids, or machine-specific absolute paths.
- Add a short note to the PR describing what changed and how you verified it.

## Areas that need help

- End-to-end verification on macOS and Linux.
- New tools that stay within Bilibili public data (no private/login-only data).
- Better handling of Bilibili risk-control changes.
