# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This is the DigiCyfr fork (`cyfrhq/brightbean-studio`) of BrightBean Studio, a self-hosted Django social media manager. Read `AGENTS.md` first: its rules (minimal TikTok Business scopes, publishing disabled until explicit content authorization, deny-by-default public gateway, synthetic/mocked data only in tests, AGPL source offer) apply to every change.

## Commands

```bash
pytest                                        # all tests (DJANGO_SETTINGS_MODULE=config.settings.test via pyproject)
pytest tests/providers/test_tiktok_business.py              # one file
pytest tests/test_studio_gateway.py -k callback             # one test by name
ruff check . && ruff format --check .         # lint (make lint); `make format` auto-fixes
mypy apps/ config/ providers/ tests/ --ignore-missing-imports   # make typecheck
python manage.py runserver                    # web
python manage.py run_studio_worker            # background worker (the Makefile's `process_tasks` target is stale)
cd theme/static_src && npm run start          # Tailwind watcher
```

`config/settings/test.py` hardcodes PostgreSQL (`brightbean_test`, `DB_HOST`/`DB_USER`/`DB_PASSWORD`/`DB_PORT` from env, default localhost:5432), so tests need a reachable Postgres even though dev can run on SQLite. Settings selection: `manage.py` → development, `wsgi/asgi` → production, pytest → test.

## Architecture

- **`providers/`**: one class per platform, all subclassing `SocialProvider` (`providers/base.py`: OAuth, `publish_post`/`check_publish_status`, metrics, inbox, webhooks, `_request`/`_poll_until` helpers). `PROVIDER_REGISTRY` in `providers/__init__.py` maps `PlatformCredential.Platform` values to classes; `get_provider()` instantiates with app credentials. Adding a platform also means a `Platform` choice + migration in `apps/credentials` and `apps/social_accounts`.
- **`apps/`**: Django apps. Org → workspace → members (RBAC in `apps/members`). Key flows:
  - `apps/social_accounts`: OAuth connect/callback views, encrypted tokens, `oauth_aliases.py` (callback slugs like `social1`/`social2`), `business_media.py` (isolated temporary media delivery for TikTok Business/Meta).
  - `apps/composer`: post editor (per-platform overrides, TikTok/Meta disclosure fields in `views.py` + `templates/composer/compose.html`).
  - `apps/publisher/engine.py`: publishes `PlatformPost` rows, runs pre-publish guards, retries, first comments.
  - `apps/api` (django-ninja REST at `/api/v1/`, routers per resource) and `apps/mcp` (JSON-RPC MCP at `/api/v1/mcp`) share auth/permissions/rate limits from `apps/api_keys`. Keys are workspace-scoped, account-allowlisted, with permission keys `create_posts`, `publish_directly`, `upload_media`, `view_analytics`, `use_inbox`, `reply_from_inbox`.
  - Background jobs use `django-background-tasks`; `apps/common/worker.py` (`StudioWorker`) runs publishing on reserved threads separately from general tasks.
  - `apps/common`: encrypted model fields and workspace-scoped managers used throughout.
- **`studio_gateway/`**: a standalone WSGI app (no Django, no DB, no secrets) deployed separately (`deploy/business-gateway/`) as the only public surface at `studio-connect.digicyfr.com`. It validates OAuth callback params (normalizing `code` → `auth_code`, rejecting duplicates/unknowns) and redirects to one fixed private callback, serves the TikTok URL-verification file, and serves media only with signed tokens (`tokens.py`) when delivery is enabled. Never log request URIs or query strings there.
- **UI**: Django templates + HTMX + Alpine.js (`templates/`, `static/js/`), Tailwind via `theme/`.

## Deployment context

The live stack is outside this repo at `/srv/cyfr/stacks/brightbean-studio/` (root-owned; `source/` is this fork as a submodule, `deploy/Dockerfile.business` overlays tested source on the security-patched base image). The app is private (UI `127.0.0.1:18080`, TLS API `:18081`, reached by SSH tunnel). Live-stack changes (rebuild, migrate, restart, secrets, backups) need explicit owner approval; take `sudo cyfr-brightbean backup` before them and rebuild the corresponding-source archive afterwards. Ops notes: `/srv/cyfr/stacks/brightbean-studio/README.md` and `AGENTS.md`.
