# Seed guard (FIX 4.8) — seed.py 275 lines
Only run when ENV != prod; guard with `if settings.ENV == 'dev': seed.run()`. Never seed on production DB.

Authoritative Technical References:
- [06_DATA_MODEL.md](../documents/06_DATA_MODEL.md) — Schema definitions and seed boundaries
- [15_SAFE_DATA_AUDIT.md](../documents/15_SAFE_DATA_AUDIT.md) — Read-only auditing of test and seed data classifications

