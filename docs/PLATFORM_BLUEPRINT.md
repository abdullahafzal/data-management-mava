# What We Are Building — Simple Explanation

> This document explains our data system in plain language.
> No technical background needed to read this.

---

## Table of Contents

1. [The Big Picture — What This System Does](#1-the-big-picture)
2. [The Problem We Had Before](#2-the-problem-we-had-before)
3. [How the New System Works](#3-how-the-new-system-works)
4. [Where the Data Comes From](#4-where-the-data-comes-from)
5. [How We Save Money on Verification](#5-how-we-save-money-on-verification)
6. [The Journey of One Lead](#6-the-journey-of-one-lead)
7. [What Happens When Someone Replies](#7-what-happens-when-someone-replies)
8. [Our Database — Explained Simply](#8-our-database--explained-simply)
9. [The Six Steps of Our Pipeline](#9-the-six-steps-of-our-pipeline)
10. [What Order We Build Things In](#10-what-order-we-build-things-in)
11. [Things We Must Decide First](#11-things-we-must-decide-first)

---

## 1. The Big Picture

We are building a system that:

1. **Collects** business owner contact info from multiple data sources
2. **Cleans and organizes** it so there are no duplicates
3. **Verifies** emails and phone numbers before spending money on campaigns
4. **Sends** outreach via email (Smartlead) and SMS (SimpleTexting)
5. **Tracks** every reply back to the exact contact that worked
6. **Pushes all verified good contacts** into GHL (our CRM) for the sales team to work

Think of it like a very smart filing cabinet that also remembers everything it has ever done.

---

## 2. The Problem We Had Before

Imagine you have a list of 10,000 business owners. You get data from five different companies
(Spokeo, Spydialer, Outscraper, ICM, BeenVerified). All five give you overlapping information.

**Old way — what goes wrong:**

- The same owner appears in all five files → you now have 5 rows for 1 person
- You send their email to MillionVerifier to check if it works → you pay 5 times for 1 email
- Someone replies to your email → you have no idea which file that contact came from
- You text the same person who is already in an email conversation with you → they get annoyed
- Unverified, messy contacts go into GHL → your sales team wastes time on bad data

**New way — what we fix:**

- 1 person = 1 record, no matter how many sources mentioned them
- 1 email = checked once, result saved forever (or until it goes stale)
- Every reply is automatically traced back to the exact phone/email that worked
- The system automatically pauses all other outreach the moment someone replies
- Only **verified, good contacts** go into GHL — the sales team works clean, quality data

---

## 3. How the New System Works

### The Most Important Rule

**Every phone number and email address is its own record.**

Not a column on a spreadsheet row. Its own record with its own ID (like a passport number).

This ID (we call it `contact_point_id`) travels with that contact everywhere:
- When we send it to Smartlead for email campaigns
- When we send it to SimpleTexting for SMS
- When a reply comes back from either platform
- When we push it into GHL

When a reply arrives, the system reads that ID and instantly knows:
- Whose phone/email this is
- Which data vendor gave us this contact
- What confidence score that vendor gave it
- How much we paid to get it
- Whether we have verified it before

Without this ID, a reply arrives and we just know "someone replied" — we don't know anything else.

---

## 4. Where the Data Comes From

We buy data from multiple vendors. Each vendor gives us a spreadsheet. The same business
owner might be in all of them.

| Vendor | What they give us | Status |
|--------|------------------|--------|
| Spydialer | Phone numbers | Active |
| ICM | Business info | Active |
| Skopio | Business info | Active |
| Outscraper | Google Maps data | Active |
| BeenVerified | Owner contact info | Active (20% data yield) |
| Spokeo | Owner contact info + confidence scores | Active |
| InstantCheckmate | Owner contact info | Evaluating |
| IDI Core | People data | Evaluating |
| TLLO | People data | Evaluating |
| Whitepages | Contact lookup | Meeting scheduled |

**The problem with multiple vendors:**

If Spokeo says the owner's email is `john@business.com` and InstantCheckmate says the same
thing — that's great, two sources agree. But we should store this as:

- One email record (`john@business.com`)
- Two notes saying "Spokeo confirmed this" and "InstantCheckmate confirmed this"

Not two separate email columns or two duplicate rows.

**Why this matters for money:** If two vendors both returned the same email, and we verify
it twice, we wasted one credit. Across 10,000 records that adds up fast.

---

## 5. How We Save Money on Verification

### The Verification Problem

Checking whether an email is valid costs money (MillionVerifier charges per credit).
Checking whether a phone is valid also costs money (XVerify charges per credit).

**The expensive mistake:** checking the same email every single time you see it.

### Our Solution — The Verification Cache

Every time we check an email or phone, we save the result permanently in our database.

Before we ever send an email or phone to a verification service, we check:
- "Have we checked this before?"
- "Is the result still fresh?"

If yes — we use the saved result. We spend nothing.
If no — we check it, then save the result for next time.

### How long results stay valid

| Result | How long we trust it | Reason |
|--------|---------------------|--------|
| Valid | 6 months | Emails do change over time |
| Invalid | Forever | No point checking again |
| Temporary inbox | Forever | Junk, never worth it |
| Uncertain | 30 days | Check again soon |
| Domain accepts everything | 3 months | Changes often |

### The Top-3 Rule

We don't check every email and phone for every owner. We only check the **3 best-scored**
contacts per person. If any one of them comes back valid, we stop — we have what we need.

**The math:**
- Without this rule: up to 360,000 verification checks for 10,000 owners
- With this rule: roughly 35,000–60,000 checks
- **Savings: roughly 85% fewer API calls**

---

## 6. The Journey of One Lead

Here is what happens from the moment a vendor file lands in our system to the moment a
salesperson opens a contact in GHL.

```
1. Vendor file uploaded
   └── System reads each row
   └── Saves verbatim copy first (so we can re-run if anything goes wrong)

2. Cleaning
   └── Phone numbers standardized: (914) 636-3563 → +19146363563
   └── Emails lowercased: VBish@Cox.NET → vbish@cox.net
   └── Gmail trick: nimsi.bashi@gmail.com = nimsibashi@gmail.com (same inbox)

3. Matching
   └── Is this business already in our database? (matched by Facility Number)
   └── Yes → add new contact info to existing record
   └── No → create new business record

4. Scoring
   └── How confident are we this email/phone belongs to THIS owner?
   └── Score based on: name match, how many vendors agree, vendor confidence, how recent

5. Verification (only top 3 per person)
   └── Check cache first → already verified? Use saved result, spend nothing
   └── Not in cache → send to MillionVerifier / XVerify → save result

6. Push verified good contacts to GHL
   └── All contacts that came back "valid" from verification go into GHL
   └── GHL does not charge per contact — all plans include unlimited contacts
   └── Sales team now has a clean, verified list to work from

7. Send campaigns via Smartlead (email) and SimpleTexting (SMS)
   └── Select verified contacts only
   └── Skip anyone on the suppression list (opted out, hard bounced)
   └── Skip anyone already in conversation (hold_until date)
   └── Send contact_point_id as a custom field with every send

8. Reply arrives
   └── System reads contact_point_id from the reply
   └── Traces back to: which owner, which vendor, which email, which cost
   └── Classifies reply (wants to talk / not interested / wrong person / etc.)
   └── Pauses all other outreach to this owner
   └── Updates the contact in GHL with the reply status
```

---

## 7. What Happens When Someone Replies

This is the most important part — it's where we close the loop.

### The 6 Things We Do When a Reply Arrives

**Step 1 — Save the reply**
We record everything: when it came in, from which platform, what it said.

**Step 2 — Classify the reply**
We put it into one of six buckets:

| What they said | Category | What we do |
|---------------|----------|------------|
| "Yes, interested" / "Tell me more" | Positive | → Update GHL contact, alert sales team |
| "Who is this?" / "What company?" | Neutral | → Update GHL contact, alert sales team |
| "Not now, maybe later" | Not now | → Schedule follow-up in GHL |
| "Not interested" | Negative | → Stop outreach, note in GHL |
| "Remove me from your list" | Opt-out | → Never contact again, saved permanently |
| "You have the wrong person" | Wrong person | → See below — this is very valuable |

**Step 3 — Update the contact record**
Mark this email/phone as "responded." A real reply is the best proof an address works —
better than any verification service.

**Step 4 — PAUSE all other outreach** ← Very important, easily missed
The moment someone replies, we stop all SMS campaigns, all other email sequences for that
owner. We don't want to text someone who is already in conversation with us over email.
Getting this wrong destroys more deals than bad data does.

**Step 5 — Update in GHL**
Since the contact is already in GHL (we pushed them after verification), we update their
record with the reply information — which channel they replied on, what they said, and
what the next action is. The sales team sees everything in one place.

**Step 6 — Update the owner profile**
Record when they replied, on which channel, through which contact.

---

### Why "Wrong Person" Replies Are Valuable

When someone says "you have the wrong guy, I'm not Van" — that reply is free information.

We know:
- That specific email doesn't belong to this business owner
- Which vendor gave us that email
- What confidence score they gave it

Over time, after getting 200–300 of these, we can calculate:
> "Spokeo emails with less than 30% confidence are wrong 40% of the time."

Now we can raise our threshold with real data instead of guessing. Every wrong-person reply
makes the system smarter for the next campaign.

---

## 8. Our Database — Explained Simply

Think of the database as a set of filing cabinets, each for a different type of information.

### Cabinet 1 — Businesses (Facilities)
One folder per business. Contains: business name, address, county, business type, how long
they've been registered, whether their registration is active or expired.

*The folder label = the Facility Number from the state registry. We never make up our own.*

### Cabinet 2 — Owners
One folder per person. Linked to their business folder. Contains: name, age, lead score,
whether we're currently in contact with them, and their GHL contact ID once they've been
pushed into GHL after verification.

### Cabinet 3 — Contact Points ⭐ Most important
One card per phone number or email address. Contains: the contact value, how confident we
are it belongs to the owner, whether it's been verified, and whether someone has replied
through it.

*Every card has a unique ID (like a passport number) that travels with the contact everywhere.*

### Cabinet 4 — Source Notes
For each contact card, one note per vendor that gave us that contact. Contains: which vendor,
their confidence score, which slot it appeared in (Phone 1 vs Phone 11), when we got it,
what we paid.

### Cabinet 5 — Verification Log
Every time we check an email or phone with a verification service, we write a new line here.
We never change old lines — it's a permanent history.

### Cabinet 6 — Campaign Events
Every time we send an email, get a reply, get a bounce, or someone opts out — one line added
here. Written automatically by the system when replies come in.

### Cabinet 7 — Do Not Contact List (Suppressions)
A list of emails and phones we must never contact again. Opt-outs, hard bounces, complaints.
This list is checked before every single send. Even if we re-import data, this list survives.

---

## 9. The Six Steps of Our Pipeline

When a new vendor file comes in, it goes through six steps automatically:

| Step | Plain English |
|------|-------------|
| **1. Save raw copy** | Keep the original file untouched. If anything goes wrong, we can start over. |
| **2. Flatten** | Every vendor uses different column names. We reshape all of them into one standard format. |
| **3. Clean** | Fix phone formats, lowercase emails, strip Gmail tricks, split names into first/last. |
| **4. Match** | Is this business already in our database? Match by Facility Number only — not by name. |
| **5. Score** | Give each contact a quality score. How confident are we it belongs to this owner? |
| **6. Load** | Add verified, scored contacts to our main database. Safe to run twice — won't create duplicates. |

---

## 10. What Order We Build Things In

We build in this order so that every phase delivers immediate value:

| Phase | What we build | Why this order |
|-------|--------------|----------------|
| **Phase 1** | Verification cache | Saves money from day one. Build this first. |
| **Phase 2** | Raw landing tables + file flattening | Makes every vendor file re-processable |
| **Phase 3** | Phone/email/name cleaning rules | Cuts duplicate count immediately |
| **Phase 4** | Core database tables + matching | The foundation everything else sits on |
| **Phase 5** | Quality scoring | Tells us which contacts to try first |
| **Phase 6** | Push verified contacts to GHL | Sales team gets clean data immediately |
| **Phase 7** | Send campaigns with contact ID as custom field | Closes the attribution loop |
| **Phase 8** | Webhooks + suppression — replies update GHL automatically | Full feedback loop live |
| **Phase 9** | Dashboard — cost per positive reply visible | Team can see what's working |
| **Phase 10** | Adjust scoring weights using real data | System gets smarter from outcomes |

---

## 11. Things We Must Decide First

Before we write a single line of code, we need answers to these questions:

**Question 1: Can one person own multiple businesses?**

Almost certainly yes. If so, the system needs to know that "John Smith, owner of Business A"
and "John Smith, owner of Business B" are the same person — otherwise we enrich him twice,
send him two separate campaigns, and split his reply history across two records.

*Decide this before designing the database.*

**Question 2: Does every vendor export include the Facility Number?**

Our system matches everything using the Facility Number. If a vendor file doesn't include it,
we can't match their data to our existing records. One vendor file we've seen (Spokeo) did not
include it. We need to fix that before the next export.

**Question 3: How do we want to classify replies?**

Three options:
- **Keyword rules** — fast, cheap, misses subtle cases ("call me next month")
- **AI pass** — catches everything, costs a couple of dollars per month at our volume
- **Human review for unclear replies** — most accurate, fast enough for now

A mix of keyword rules for obvious cases + human review for unclear ones is probably the
right start.

**Question 4: What is our GHL plan?**

GHL does not charge per contact — all plans include unlimited contacts. Pricing is a flat
monthly fee ($97, $297, or $497/month depending on the plan). What GHL charges extra for is
actual usage — each SMS sent, each email sent, each phone call made. Plan accordingly.

---

## Quick Reference — Key Terms

| Term | Plain meaning |
|------|-------------|
| `contact_point_id` | The unique ID we give every email/phone. Like a passport number. Travels everywhere. |
| Verification cache | Our saved results from past email/phone checks. Prevents paying twice. |
| Top-3 rule | Only check the 3 best contacts per person. Stop when one is valid. |
| is_primary | The best contact for this person on this channel. The one we send to first. |
| Suppression list | The permanent "never contact again" list. Survives everything. |
| GHL | GoHighLevel — our CRM. Receives all verified good contacts. Unlimited contacts, flat monthly fee. |
| reply_class | Which bucket a reply falls into (positive / opt-out / wrong person / etc.) |
| hold_until | A date that tells the system "don't contact this person until this date." |
| Facility Number | The state registry's ID for a business. Our master matching key. |
| Lead score | A 0–100 number saying how likely this owner is to sell their business. |

---

*Last updated: August 2026*
