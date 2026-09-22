# Making the house follow the masjid

Iqama times move through the year, so a routine set to "Every day, at 6:05 PM"
is right for about a week. Floating Clock can run those routines instead, at
whatever the masjid's calendar says for that day.

There are three ways to do it, and which one you want depends on what you have:

| | **Trigger a routine** | **Play on the speaker** | **Play on this computer** |
|---|---|---|---|
| Works with | Alexa, and Google via Home Assistant or IFTTT | Google Home, Nest, Chromecast | any Windows, Mac or Linux machine |
| Setup | a trigger skill or a webhook, once per prayer | pick your speaker from a list | pick a file, once |
| Can do | anything the routine does — lights, volume, several speakers | play your adhan | play your adhan |
| Needs | an account with the trigger service | nothing; it is all on your network | nothing at all — no speaker, no account, no network |

If all you want is the adhan on a Google or Nest speaker, use the speaker — it
is the shorter road by a long way. Use a routine when something else has to
happen too, or when you are on Alexa. Use **Play on this computer** when
there is no speaker to reach at all — travelling with just the laptop, most
of all.

## Where the times come from

Set this first; both methods hang off it.

Open **Calendar → Prayer times…** and press **Find my masjid…**. Type a town,
an address, a postal code or a masjid's name, and pick yours from the list.
That is the whole setup: nothing to look up and no address to find.

Or press **Near me**, and the list is the masjids within 20 km of where this
computer is, nearest first, with how far each is. The clock asks Windows where
the computer is, the way any program can (Settings → Privacy & security →
Location, with *Let desktop apps access your location* on). If Windows will not
say, or on a Mac or Linux computer, it works it out from the computer's
internet address instead — one request to a public lookup service, which sees
only that address — and says so, because that puts you in the right town rather
than on the right street; if it is out, type your town instead. The position is
used for that one search. It is not kept, and it is not written to the
settings.

The list is put together from three places at once:

- **OpenStreetMap**, searched live. Everything mapped as a masjid or prayer
  room near the place you typed, with its website when the map has one. It is
  free and needs no account, and it is not complete: a well-known centre can
  be missing altogether. (Map data © OpenStreetMap contributors.)
- **mawaqit.net**, searched live. Several thousand masjids, and the ones it
  carries come with a whole year of congregation times.
- **A directory that ships with the clock** — 355 masjids with their
  addresses and websites, almost all of them in Ontario. It answers instantly
  and works with no network.

### How the clock gets the times for the masjid you pick

The way you would: it goes to the masjid's website and reads them. It tries,
in this order, and stops at the first that works.

1. **The masjid's mawaqit.net page** — a year of congregation times.
2. **The masjid's own timetable plugin**, if its site runs the common
   WordPress one.
3. **A mawaqit, PrayersConnect or Masjidbox page the site embeds.**
4. **An iqamah iCal calendar**, if the site publishes one with prayers in it.
5. **The website itself** — its home page, the pages it links to as prayer
   times, and the frames it embeds. If the plain page holds nothing, the same
   pages again in a real browser (Edge, Chrome or Chromium, run invisibly and
   thrown away after), for the many sites whose times are written in by a
   script and are not in the page a program is sent.

The first four are data feeds and exact. The fifth is reading a web page, and a
page can be out of date, half filled in or laid out in a way that fools a
reader, so the clock treats it warily:

- it is only accepted if it is a timetable a masjid could actually have: the
  five prayers in order, each iqama after its adhan, no template of round
  numbers waiting to be filled in;
- a date printed beside the times has to be today's (or "from" a date that has
  passed, or "changes on" one still to come);
- and it has to agree with the sun. Maghrib cannot fall two hours after
  sunset, and Fajr cannot come before first light, so a June timetable left up
  in September is refused however well it is laid out. Maghrib written as
  "Sunset" or "5 minutes after sunset" is worked out from where the masjid is.

And **the clock shows you what it found before it keeps it.** Press *Use this
masjid* and the five times appear for you to check against the masjid -- whether
they came from a page or a feed, since a listing can be out of date too -- with a
note if the sun objects to one of them; press *Done* to keep them, or *Back to
results* to look at another masjid.

### When a masjid publishes nothing the clock can read

Many masjids publish no daily iqama times online at all — only Jumu'ah, or a
picture or PDF, or nothing — and no reader can get what is not there. Then the
clock does what you would: it looks at the masjids around it, nearest first, and
offers the first whose times it can read. It says whose they are —

> *X publishes no times the clock can read. The nearest masjid that does is Y
> (3.2 km away): … These are Y's times, not X's, and may differ from what X
> announces.*

— and keeps them only if you say so, and the settings page says they are
approximate for as long as they are. Times from a couple of kilometres away,
a few minutes out, are far more use than none; they are not the masjid's own
and the clock never presents them as such.

### How much of this works

Tested on 160 masjid websites — the directory's, plus those of masjids on
mawaqit.net; mostly Ontario, with some in every province:

| | Masjids |
|---|---|
| Read from a data feed (mawaqit, the site's plugin) | 34 |
| Read off the page itself | 44 |
| **Their own times, in all** | **78 of 160 (49%)** — 58% of the 134 whose sites answered |
| Not read, but a readable masjid within 25 km | a further 53 |
| **Their own times, or a neighbour's** | **131 of 160 (82%)** |

The rest publish nothing to read. Looking at a dozen of the unread sites the way
a person would, most give no daily times at all — a school, a Jumu'ah time and
nothing else, an address that belongs to another masjid. Times posted as a
picture or a PDF, and the few widgets that show start times only, are not read
yet. So a masjid's *own* times from its website will not reach 90% however well
it is read; that takes a masjid that publishes them, or somebody typing them in.
Coverage differs by place: it is best where masjids are on mawaqit.net or use a
common plugin, and worst in small towns.

### When a masjid is refused

The clock would rather show nothing than show the wrong time, so it refuses:

| What it found | Why it is not used |
|---|---|
| A website with no timetable it can read | nothing to read |
| A timetable that stops at an old year | the masjid has not updated it |
| Times that cannot be prayer times (Fajr at 00:57) | the plugin is misconfigured |
| "Congregation" times equal to the start times | the masjid never entered its iqama, and using them would call the azan up to an hour early |
| A mawaqit page with iqama switched off | the masjid chose not to publish them |
| A page whose date is not today's | it has not been updated |
| A page whose times the sun contradicts | a season out of date, or a template |
| A page with several different sets and no way to tell today's | it might be the wrong day, or the women's hall |
| A masjid in another time zone | the clock shows times on this computer's clock |

All of these were met on real masjids while testing.

### Or paste an address

The **Masjid** box still takes an address directly, and the clock works out
what it is:

| Paste this | What happens |
|---|---|
| `mawaqit.net/en/<masjid>` | a year of congregation times |
| A masjid's own website | read as described above: its timetable plugin, an embedded mawaqit, PrayersConnect or Masjidbox page, a calendar, or the page itself. A vanity domain that redirects (kitchenermasjid.com) is followed to where the masjid really lives |
| `prayersconnect.com/mosques/<masjid>` | today's congregation times |
| An iqamah iCal address | read as a calendar |

Leave it empty for Waterloo Masjid, which is built in.

The clock keeps what it reads on this machine and checks for changes every
night at 02:30, catching up within a minute if the machine was off or asleep.
Any iqama that moves is written to the log and shown on the Prayer page. So
it stays right through the seasons and through Ramadan without being touched.

Mawaqit and the WordPress plugin both hand over a year at a time, so the
clock still knows today's times with the network down. A PrayersConnect page
carries today only, so a machine that has been off for a day starts with
nothing until it has been online a moment.

---

## Playing the adhan on a Google or Nest speaker

Google Home has no way to start a routine from outside. Its automations begin
from a time, from the sun, or from a device's state, and none of those is
something a clock on a desk can move. So for Google the clock does not use a
routine at all — it plays the adhan on the speaker itself, over your network.

That turns out to be the better half of the bargain: nothing to link, no skill
to enable, and the volume is set on the same connection as the audio, so it
cannot play at last night's volume the way an Alexa routine can.

1. Open **Calendar → Prayer times…** and find the **Google or Nest speaker**
   card.
2. Press **Find speakers**. Your speakers and speaker groups appear by the
   names they have in the Google Home app. If only one answers it is filled in
   for you; otherwise copy the one you want into the box.
3. Choose the **Adhan** — a file on this machine (press *Choose…*) or a link.
   Nothing is shipped with the clock, so this is your own recording.
4. Set **Play this long before iqama**, and the **volume**.
5. Press **Test**. The adhan should start on the speaker there and then.
6. Switch on **Play the adhan on a speaker at each prayer**.

Fajr's adhan has a line the others do not, so give Fajr its own file in the
row beneath; any prayer left blank uses the one at the top.

**What it needs.** The machine has to be awake, and on the same network as the
speaker. A local file is served to the speaker from this machine for a few
minutes while it plays; a link is fetched by the speaker itself.

---

## Playing the adhan on this computer

No speaker, no assistant, no account — the clock plays the adhan itself,
through whatever this machine's own speakers or headphones are. This is what
is still there when travelling with just the laptop, or anywhere else none of
the above reaches.

Nothing is installed to do it. Windows, macOS and Linux each already carry
something that can play a file, so the clock uses that: Windows' own MCI
service, macOS's `afplay`, and on Linux whichever of a few common players is
actually on the machine.

1. Open **Calendar → Prayer times…** and find the **Play on this computer**
   card, under the speaker card.
2. Choose the **Adhan** — a file on this machine (press *Choose…*) or a link.
   Nothing is shipped with the clock, so this is your own recording.
3. Set **Play this long before iqama**, and the **volume**. Not every format
   on every system can be set from here; where it cannot, playback is left at
   whatever the system is already playing at.
4. Press **Test**. The adhan should start right there.
5. Switch on **Play the adhan here at each prayer**.

Fajr's adhan has a line the others do not, so give Fajr its own file in the
row beneath; any prayer left blank uses the one at the top.

**What it needs.** The machine has to be awake. A link is downloaded to a
temporary file first and deleted again once it has played; nothing about it
is kept. On Linux, if none of `paplay`, `ffplay`, `mpg123` or `cvlc` is
installed, the card says so — installing any one of them is enough.

---

## Triggering a routine

Amazon has no API for creating or editing a routine — their own developer
forum says so, and the only official routine APIs are for *triggering* one,
not for changing its time. So the clock does not try to edit anything.

Instead the routine stops using a time at all. Alexa routines can be started
by a device, and a trigger skill turns a plain web address into such a device.
The routine's **When** becomes that trigger; the clock calls the address at
the right moment each day. Nothing about the routine's actions changes, and
nothing needs editing again — not for the seasons, not for Ramadan.

### Alexa

**1. Make a trigger for each prayer.**
Go to <https://www.virtualsmarthome.xyz/url_routine_trigger/> and sign in with
the same Amazon account your Echo devices use. Create one trigger per prayer —
`Fajr`, `Dhuhr`, `Asr`, `Maghrib`, `Isha`. You only have to give each a name;
a web address is generated for it straight away.

**2. Turn on the skill.**
In the Alexa app: **More → Skills & Games**, search for *URL Routine Trigger*,
enable it, and sign in with that same Amazon account.

**3. Let Alexa find the triggers.**
In the Alexa app: **Devices → + → Add Device → Other → Discover devices**.
The triggers you made appear as doorbells.

**4. Point each routine at its trigger.**
Open a routine you already have — say **Asr**, which sets the volume to 10 and
asks the prayer-time skill to play the azan. Under **When**, remove
"Every Day, at 6:05 PM" and add **Smart Home → Asr → Doorbell press** instead.
Leave everything under **Alexa will** exactly as it is.

**5. Paste the addresses into Floating Clock.**
**Calendar → Prayer times…**, then the **Routine triggers** card:

- Switch on **Call a trigger address at each prayer**.
- Set **Fire this long before iqama**. The azan is called before the
  congregation stands, so this usually wants to be ahead of the iqama time —
  ten minutes matches a 6:05 PM routine against a 6:15 PM Asr iqama.
- Paste each prayer's address into its box, and press **Test** beside one.

One trigger is usually enough. If the same thing should happen at every
prayer, make one trigger, point one routine at it, paste its address into any
box and press **Same for all**.

### Google, through a routine

Google has no trigger service of its own, but anything that ends in a plain
web address works in the same boxes:

- **Home Assistant** — make a webhook automation and paste its URL. If you
  already run Home Assistant this is the shortest route, and the automation
  can then do whatever a Google Home routine could.
- **IFTTT** — a Webhooks trigger with a Google Home action.

If all you want is the adhan, use the speaker card instead. It needs none of
this.

### Anything else

Any service that hands out a plain https address works — the clock just opens
it. *Webhook Routine Trigger* and *Voice Monkey* both do this, as does a
Home Assistant webhook.

---

## Two things that catch everyone out

**Alexa: set the volume, wait, then play.** The routine's actions want to be
in this order:

1. **Set volume to 10**
2. **Wait** — about 10 seconds (*Add another action → Wait*)
3. **"Ask prayer time to play azan"**

Setting the volume is not instant. Without that pause the skill starts talking
before the new volume has taken hold, so the azan plays at whatever the Echo
was left on overnight. The doorbell chime still rings, because chimes use a
different volume from speech and skills — so the symptom is "I heard the
doorbell but no azan", and it is easy to mistake for the trigger not working
at all.

The speaker card does not have this problem: it sets the volume on the same
connection it starts the audio on.

**Maghrib follows the sun, not the iqama.** Maghrib is called at sunset, and a
masjid's Maghrib iqama is a few minutes *after* that, so firing early against
the iqama would call the azan before the sun had actually gone down.

- On Alexa, leave Maghrib's trigger empty and use a routine started by
  **Sunset**, which Alexa knows for the device's own location.
- On the speaker, either leave Maghrib's row empty, or accept that it plays a
  few minutes before the iqama — which is after sunset, so it is fine as long
  as the lead is short.

The other four have no such tie to the sun and take their times from the
masjid.

---

## What the clock does from then on

Every second it compares the masjid's times against the clock, and acts as
each moment arrives. If the machine was asleep or busy and the moment passed
more than two minutes ago, that prayer is skipped rather than fired late — an
azan two hours after the fact is worse than none. Each prayer fires once a
day, and its routine and its adhan are counted separately, so one cannot mark
the other as done. The settings page shows what happened last. A trigger that
fails — the network still waking up, the service busy — is tried again three
more times inside those two minutes.

Friday's Jumuah uses Dhuhr's trigger and Dhuhr's adhan unless it is given its
own, so the most-attended prayer of the week is never the one that quietly
does nothing.

Every night at 02:30 the clock fetches the masjid's times again and compares
them with what it had: any iqama that moved is written to the log and shown on
the Prayer page. A night the machine was off or asleep through 02:30 is caught
up within a minute of it coming back. Once an hour the log gets a line saying
the clock is alive and what fires next, so a clock that has stopped shows up
as a gap in the log rather than as silence.

If the settings file cannot be read when the clock starts — which happened at
the first sign-in after a Windows update — the clock waits for it rather than
starting on the defaults, and if it still cannot read it, says so on screen
and refuses to save anything over it.

The clock has to be running, and the machine awake, for any of this to happen.
For the speaker, it also has to be on the same network.
