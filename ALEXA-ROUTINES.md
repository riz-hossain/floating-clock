# Alexa routines that follow the masjid

Iqama times move through the year, so a routine set to "Every day, at 6:05 PM"
is right for about a week. Floating Clock can run those routines instead, at
whatever the masjid's calendar says for that day.

## Why it works this way

Amazon has no API for creating or editing a routine — their own developer
forum says so, and the only official routine APIs are for *triggering* one,
not for changing its time. So the clock does not try to edit anything.

Instead the routine stops using a time at all. Alexa routines can be started
by a device, and a trigger skill turns a plain web address into such a device.
The routine's **When** becomes that trigger; the clock calls the address at
the right moment each day. Nothing about the routine's actions changes, and
nothing needs editing again — not for the seasons, not for Ramadan.

## Setting it up once

**1. Make a trigger for each prayer.**
Go to <https://www.virtualsmarthome.xyz/url_routine_trigger/> and sign in with
the same Amazon account your Echo devices use. Create one trigger per prayer —
`Fajr`, `Dhuhr`, `Asr`, `Maghrib`, `Isha`. You only have to give each a name;
a web address is generated for it straight away. Copy each address somewhere
you can get at it from this PC.

**2. Turn on the skill.**
In the Alexa app: **More → Skills & Games**, search for *URL Routine Trigger*,
enable it, and sign in with that same Amazon account.

**3. Let Alexa find the triggers.**
In the Alexa app: **Devices → +  → Add Device → Other → Discover devices**.
The triggers you made appear as doorbells.

**4. Point each routine at its trigger.**
Open a routine you already have — say **Asr**, which sets the volume to 10 and
asks the prayer-time skill to play the azan. Under **When**, remove
"Every Day, at 6:05 PM" and add **Smart Home → Asr → Doorbell press** instead.
Leave everything under **Alexa will** exactly as it is. Repeat for each prayer.

**5. Paste the addresses into Floating Clock.**
Right-click the clock → **Calendar → Prayer times…**, then the **Alexa routines**
card:

- Switch on **Trigger an Alexa routine at each prayer**.
- Set **Fire this long before iqama**. The azan is called before the
  congregation stands, so this usually wants to be ahead of the iqama time —
  ten minutes matches a 6:05 PM routine against a 6:15 PM Asr iqama.
- Paste each prayer's address into its box.
- Press **Test** beside one. The Echo should run that routine there and then.

Friday's Jumuah uses the Dhuhr trigger unless it is given one of its own, so
the most-attended prayer of the week is never the one that quietly does
nothing.

## Two things that catch everyone out

**Set the volume, wait, then play.** The routine's actions want to be in this
order:

1. **Set volume to 10**
2. **Wait** — about 10 seconds (*Add another action → Wait*)
3. **"Ask prayer time to play azan"**

Setting the volume is not instant. Without that pause the skill starts talking
before the new volume has taken hold, so the azan plays at whatever the Echo
was left on overnight. The doorbell chime still rings, because chimes use a
different volume from speech and skills — so the symptom is "I heard the
doorbell but no azan", and it is easy to mistake for the trigger not working
at all.

**Leave Maghrib empty.** Maghrib is called at sunset, and a masjid's Maghrib
iqama is a few minutes *after* that, so firing early against the iqama would
call the azan before the sun had actually gone down. Alexa knows sunset for
the device's own location, so a routine triggered by **Sunset** is the right
home for that one. The other four have no such tie to the sun and take their
times from the masjid.

## What the clock does from then on

Every second it compares the masjid's times against the clock, and calls a
trigger's address as its moment arrives. If the PC was asleep or busy and the
moment passed more than two minutes ago, that prayer is skipped rather than
fired late — an azan two hours after the fact is worse than none. Each prayer
fires once a day; the settings page shows what happened last. A call that
fails — the network still waking up, the trigger service busy — is tried
again three more times inside those two minutes.

Every night at 02:30 the clock fetches the masjid's calendar again and
compares it with what it had: any iqama that moved is written to the log and
shown on the Prayer page. A night the PC was off or asleep through 02:30 is
caught up within a minute of it coming back. Once an hour the log gets a line
saying the clock is alive and which prayer fires next, so a clock that has
stopped shows up as a gap in the log rather than as silence.

If the settings file cannot be read when the clock starts — which happened
at the first sign-in after a Windows update — the clock waits for it rather
than starting on the defaults, and if it still cannot read it, says so on
screen and refuses to save anything over it.

The clock has to be running, and the PC has to be awake and online, for a
routine to fire.

## Other trigger skills

Any service that gives out a plain https address works the same way — the
clock just opens it. *Webhook Routine Trigger* and *Voice Monkey* both do this,
as does a Home Assistant webhook if you already run one. Paste whichever
address you end up with into the same boxes.
