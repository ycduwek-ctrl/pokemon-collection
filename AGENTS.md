# Hitim project context

Read this file before changing the project. It is the durable handoff for new
coding chats.

## Product and user preferences

- Communicate with the owner in Hebrew and use simple, direct explanations.
- Routine, non-destructive implementation, tests, GitHub PRs, merging to
  `main`, and deployment verification are already authorized. Do not repeatedly
  ask for approval for those steps.
- Use free services only. Do not add a paid dependency or a flow that requires
  billing.
- Reliability and phone performance are more important than decorative effects.
- Preserve existing galleries and user data. Never delete or migrate them
  without an explicit, verified backup plan.

## Architecture

- Repository: `ycduwek-ctrl/pokemon-collection`.
- The static PWA is deployed by Vercel from `main`.
- The FastAPI backend is deployed by Render from `main` at
  `https://pokemon-collection-f4wv.onrender.com`.
- Google login and the access list use Supabase. Secrets are Render environment
  variables and must never be committed or printed.
- Each user's cards, photos, comments, and gallery backup live in that device's
  IndexedDB through `hitim-db.js`; they are not saved to Sheets or Cloudinary.
- `card_catalog.py` opens `data/card_catalog.sqlite3.gz`, a local multilingual
  TCGdex-derived catalog with more than 130,000 searchable printings.

## Identification and pricing

- Quick identification first runs Tesseract OCR locally in the browser and
  sends only the read text to `/catalog/identify-text`. Collector number,
  denominator, set code, and printed name are resolved against the bundled
  catalog.
- If local OCR cannot produce one safe match, `/identify` uses the current free
  OpenRouter vision model and then verifies its hints against the same catalog.
- Do not make live TCGdex search a blocking part of identification.
- Price lookup is a separate `/price` request. A price-source failure must not
  turn a successful identity into a failed scan.

## Verification and release

- Run `python -m unittest discover -s tests -v`, Python compilation, and a
  JavaScript syntax check before publishing.
- Merge the PR to `main`, then verify `/health` reports the expected build and a
  ready catalog. Also verify the Vercel shell contains the new build marker.
- The service worker uses a versioned shell cache. Bump it when changing the app
  shell so installed phones receive the new release immediately.
