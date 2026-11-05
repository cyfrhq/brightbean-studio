# Business API preparation

Status: unapproved, publishing disabled. No live app credentials or clients in this source.

## Public endpoint plan (requires exact Domain Operations approval)

- `studio-connect.digicyfr.com` → existing `cyfr-odoo` Cloudflare Tunnel
  `5943ce2e-bd3c-48ff-a476-ed05c0fe84aa`, service `http://127.0.0.1:18082`,
  Host header `studio-connect.digicyfr.com`.
- A single-level hostname fits the existing zone wildcard TLS certificate.
- `/social2/callback/` is the registered TikTok account holder redirect URL.
  It forwards only bounded authorization parameters to the fixed private app
  callback. The app verifies signed state, expiry, session nonce, user,
  workspace membership and permissions before exchanging a code.
- `/advertiser/callback/` explains that advertiser access is not configured;
  no advertising scopes, token exchange or advertising operations are enabled.
- Media serving starts **off**. After app/scope approval and URL ownership
  verification, authorized dispatch can mint encrypted URLs valid for one hour.
  Only the selected video is fetchable; the raw media directory is not served.
- Verification files are served only by exact entries in `verification.json`.
  Add only the provider's actual challenge after checking its filename/content.
- UI, database, secrets, Odoo and main marketing site retain their access.

## App settings when approved for connection

```
TIKTOK_BUSINESS_REDIRECT_URI=https://studio-connect.digicyfr.com/social2/callback/
TIKTOK_BUSINESS_MEDIA_ORIGIN=https://studio-connect.digicyfr.com
TIKTOK_BUSINESS_MEDIA_KEY_FILE=/run/secrets/business-media.key
TIKTOK_BUSINESS_PUBLISH_ENABLED=false
```

Store `client_id`, `client_secret`, and the exact portal-generated **TikTok
account holder authorization URL** as the `tiktok_business` encrypted org
credential. Never reuse creator-app credentials or an advertiser auth URL.
Register only `Get account user basic info` and `Video publish` initially.

Use a newly generated Fernet key in the existing root secret store, readable
only by the service UID, mounted read-only into the app, worker and gateway.
Never commit or print it. The gateway has no app environment or database route.
Verify the external media volume name before starting this Compose file.

## Activation gates

1. Approved Business developer profile, created/reviewed Business app and
   Accounts API scope access. A submitted form is not approval.
2. Approved exact DNS/tunnel plan; anonymous HTTPS verification of callbacks.
3. Media URL ownership verified in the Business app's URL properties.
4. Deploy tested corresponding source, run migrations after a verified backup,
   preserve the AGPL source offer, install dedicated encrypted credentials.
5. Owner OAuth grant for each account and correct workspace mapping.
6. Explicitly authorize each real publication. No publishing is performed by
   setup or tests. Turn on media serving and publishing only when ready.

## Rollback

Remove only the new tunnel hostname/record; stop the separate gateway Compose
project. Disable Business publishing, restore the previous app image/source
and configuration using the verified backup if deployed. Existing creator
TikTok, Facebook, Instagram, private app and Odoo routes are preserved.
