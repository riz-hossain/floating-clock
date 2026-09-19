# Making the house follow the masjid

Iqama times move through the year, so a routine set to "Every day, at 6:05 PM"
is right for about a week. Floating Clock can run those routines instead, at
whatever the masjid's calendar says for that day.

There are two ways to do it, and which one you want depends on what you have:

| | **Trigger a routine** | **Play on the speaker** |
|---|---|---|
| Works with | Alexa, and Google via Home Assistant or IFTTT | Google Home, Nest, Chromecast |
| Setup | a trigger skill or a webhook, once per prayer | pick your speaker from a list |
| Can do | anything the routine does — lights, volume, several speakers | play your adhan |
| Needs | an account with the trigger service | nothing; it is all on your network |

If all you want is the adhan on a Google or Nest speaker, use the speaker — it
is the shorter road by a long way. Use a routine when something else has to
happen too, or when you are on Alexa.

## Where the times come from

Set this first; both methods hang off it.

Open **Calendar → Prayer times…** and put your masjid's **website** in the
Masjid box — the ordinary address people visit, like
`https://centres.macnet.ca/icwaterloo/`. A great many masjid sites publish
their timetable in a way the clock can read directly, and it takes the
congregation (iqama) times, not the adhan times.

An iqamah **iCal address** works too, if your masjid publishes one. Leave the
box empty for Waterloo Masjid, which is built in.

The clock keeps a year of times on this machine and checks for changes every
night at 02:30, catching up within a minute if the machine was off or asleep.
Any iqama that moves is written to the log and shown on the Prayer page. So it
stays right through the seasons and through Ramadan without being touched, and
it still knows today's times with the network down.

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
