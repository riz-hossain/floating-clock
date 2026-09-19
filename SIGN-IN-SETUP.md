# Google and Microsoft sign-in: what has to be registered once

Floating Clock can already read iCloud, Zoho, Fastmail, Yahoo and any other
CalDAV calendar from an email address and an app password, and Google
calendars from their secret iCal address. "Sign in with Google" and "Sign in
with Microsoft" need the app registered with each company first. That is a
one-time task on the account of whoever publishes the app; users never see it.

Each registration produces a **client ID**. Hand the two IDs over and the
sign-in buttons get built against them.

## Google (Google Calendar, Google Workspace)

1. Go to <https://console.cloud.google.com> and create a project, e.g.
   "Floating Clock".
2. **APIs & Services → Library**: enable **Google Calendar API**.
3. **APIs & Services → OAuth consent screen**: choose **External**, give the
   app a name and a support email, save.
4. Under **Scopes**, add `https://www.googleapis.com/auth/calendar.readonly`
   (read-only is all the clock needs).
5. Under **Test users**, add the Google addresses that should work before the
   app is verified (up to 100).
6. **APIs & Services → Credentials → Create credentials → OAuth client ID**,
   application type **Desktop app**. Copy the **Client ID** (and the client
   secret; for a desktop app Google treats it as non-confidential).
7. When it is time to open it to everyone: **Publish app** on the consent
   screen, then submit for **verification**. Read-only calendar access is a
   "sensitive" scope, so Google reviews the app: it asks for a privacy-policy
   page on a domain you own and a short description of what the app does.
   Until verification is granted, only the test users can sign in.

## Microsoft (Microsoft 365, Outlook.com)

1. Go to <https://entra.microsoft.com> → **Applications → App registrations →
   New registration**.
2. Name it "Floating Clock". Under **Supported account types**, choose
   *Accounts in any organizational directory and personal Microsoft accounts*.
3. **Redirect URI**: platform *Mobile and desktop applications*, URI
   `http://localhost`.
4. After creating it, open **Authentication** and set **Allow public client
   flows** to **Yes** (a desktop app cannot keep a secret).
5. **API permissions → Add a permission → Microsoft Graph → Delegated**:
   add `Calendars.Read`, `User.Read` and `offline_access`.
6. Copy the **Application (client) ID** from the Overview page.
7. Note for company tenants: an administrator may have to approve the app
   once for their organisation (**Grant admin consent**), depending on that
   tenant's policy. Personal accounts need nothing further.

## What happens in the app afterwards

- The email-first "Add a calendar" window shows **Sign in with Google** or
  **Sign in with Microsoft** for those providers instead of asking for a link.
- Sign-in opens the provider's own page in the browser; the clock listens on
  `http://localhost` for the response. No password ever passes through the
  clock.
- The tokens go into Windows Credential Manager, exactly where CalDAV app
  passwords already live, and are refreshed silently.
