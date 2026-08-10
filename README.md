# Hitim

Hitim is a private, installable Pokémon card gallery. Google login and the
administrator's access list are centralized; every user's cards, photos and
notes are stored in that user's browser with IndexedDB.

The primary administrator is configured privately with the
`HITIM_ADMIN_EMAIL` environment variable. Do not commit that address.

## Privacy model

- New card photos are compressed in the browser and saved only on the device.
- New gallery records are not written to Google Sheets or Cloudinary.
- A selected photo is sent transiently to the existing recognition service and
  is not persisted by Hitim's backend.
- Supabase stores authentication identity and the access status/role only.
- Price and recognition requests require a valid, approved Google session.
- A blocked user loses online access, but a website cannot remotely erase data
  that was already saved in that person's device storage.

## One-time Supabase setup

1. Create a free Supabase project owned by the Hitim account.
2. Open Supabase **SQL Editor** and run [`supabase-schema.sql`](supabase-schema.sql).
3. In Google Cloud, configure an OAuth consent screen and create a **Web
   application** OAuth client.
4. Copy the exact Supabase callback shown under **Authentication → Providers →
   Google** into Google Cloud's authorized redirect URIs. It has the form
   `https://PROJECT_REF.supabase.co/auth/v1/callback`.
5. Paste the Google client ID and client secret into the Supabase Google
   provider and enable it.
6. In **Authentication → URL Configuration**, set the production Vercel URL as
   the Site URL and add the Vercel preview URL used for testing to Redirect URLs.

## Backend environment variables

Configure these directly in Render. Never commit or send their values in chat:

```text
SUPABASE_URL
SUPABASE_ANON_KEY
SUPABASE_SERVICE_ROLE_KEY
HITIM_ADMIN_EMAIL=<ADMIN_GOOGLE_EMAIL>
```

Keep the existing `OPENROUTER_KEY` and any pricing API configuration. The
service-role key belongs on the backend only; the browser receives only the
public anon key.

## Safe rollout

1. Deploy the `agent/hitim-local-galleries` branch to a separate Render test
   service and connect a Vercel preview to it.
2. Sign in with the Google account configured in `HITIM_ADMIN_EMAIL`; that exact
   account is auto-approved as the administrator.
3. Test a second Google account: it must remain pending until approved in the
   Hitim user-management panel.
4. From Hitim settings, download a backup and run the one-time legacy import.
   The Google Sheet and Cloudinary originals are deliberately left untouched.
5. Verify card identification, local save/delete, daily price refresh, backup
   restore and installation on a phone.
6. Only after verification, merge and switch production. Keep the old cloud
   data until the local import and backup have been checked.
