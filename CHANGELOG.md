# Changelog

## 1.0.0 — 2026-11-14

Initial public release.

- Four tools: `bili_user_stat`, `bili_user_videos`, `bili_video_info`, `bili_search_user`.
- Node host with `defineTool`, Python interpreter probing, dependency bootstrap, and a serialized tool queue.
- Python data layer with `curl_cffi` browser impersonation, Wbi signing and 24h key cache,
  a global 2-second request interval, `-799` / `412` / `-352` retries, and error mapping.
- Cookie loaded from a plugin-root `.env`, never logged.
- `cordis.patch.yml` bundle install plus `cordis.yml` `--patch` quick start.
- Bilingual README (English first, Chinese below) with a Bilibili intro video link in the Chinese section.
- Cover and screenshots, usage/install/architecture docs, CI syntax check, MIT license.
