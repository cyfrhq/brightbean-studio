# DigiCyfr Studio Business API integration

- Preserve the existing TikTok creator provider and connected accounts.
- Implement the separate API for Business provider with minimal scopes.
- Keep the BrightBean UI, database, credentials and raw media private.
- Public callback/media gateway must be isolated, fixed-destination and deny by default.
- Never log or commit tokens, authorization codes, signing keys or client content.
- Use synthetic data and mocked TikTok responses in tests; never publish to test.
- Publishing remains disabled until provider approval and explicit content authorization.
- Preserve AGPL notices and offer corresponding source for deployed changes.
