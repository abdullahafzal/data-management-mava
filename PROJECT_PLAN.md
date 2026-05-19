# Data Management Tool — Project Plan & Progress

**Client:** MABA / Bricks outreach team (Matt, Salvador, Diana, Claudia, Matthew)  
**Developer:** Abdullah  
**Last updated:** May 15, 2026 *(Sprint: ZIP, Diana v1, history filters, MV API helper)*  
**Source:** Meeting transcript (`transcript.txt`) + follow-up requirements

---

## 1. What the client wants (summary)

An internal web app that replaces manual work between **Outscraper → Excel cleanup → MillionVerifier → campaign tools**. The team should:

- Import Google Maps business data from Outscraper
- Avoid paying twice for the same Outscraper search (filter history + duplicate warning)
- Clean data (pick columns per campaign)
- Verify emails (and later phones)
- Export verified lists by status (good, risky, unknown, etc.)
- Eventually push leads to **Smartlead** (email) and **SimpleTexting** (SMS)
- Send “missing / failed” rows to **Diana** for manual enrichment

---

## 2. Full pipeline (target state)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         TARGET END-TO-END FLOW                              │
└─────────────────────────────────────────────────────────────────────────────┘

  [Matt] Campaign target (niche + location)
           │
           ▼
  ┌─────────────────┐     Phase 3: API later
  │   OUTSCRAPER    │ ◄── Google Maps + enrichments (email, phone, white pages)
  └────────┬────────┘
           │ CSV / XLSX
           ▼
  ┌─────────────────┐     Phase 1: ✅ DONE (manual upload + filter tags + history)
  │  IMPORT & TAGS  │ ◄── Save file, Outscraper filters, duplicate check
  └────────┬────────┘
           ▼
  ┌─────────────────┐     Phase 1: ✅ DONE
  │     CLEAN       │ ◄── Per-campaign column selection → cleaned CSV
  └────────┬────────┘
           │
     ┌─────┴─────┐
     ▼           ▼
┌─────────┐ ┌──────────────┐
│ EMAIL   │ │ PHONE        │     Phase 2: Phone path NOT STARTED
│ PATH    │ │ PATH         │
└────┬────┘ └──────┬───────┘
     ▼             ▼
┌─────────────┐ ┌──────────────────────┐
│ Million     │ │ Real Phone           │     Phase 2: NOT STARTED
│ Verifier    │ │ Validation           │
└─────┬───────┘ └──────────┬───────────┘
      │                    │
      ▼                    ▼
┌─────────────┐     ┌──────────────┐
│ Export:     │     │ Cell-only    │
│ good,risky, │     │ CSV          │
│ unknown…    │     └──────┬───────┘
└─────┬───────┘            │
      │                    ▼
      ▼              ┌──────────────┐
┌─────────────┐      │ SimpleTexting│     Phase 4: NOT STARTED
│ Smartlead   │      │ SMS campaigns│
│ email camp. │      └──────────────┘
└─────────────┘

      │  Rows missing email/phone or failed verify
      ▼
┌─────────────────┐
│  DIANA QUEUE    │     Phase 2: ✅ v1 heuristic (missing email/phone export)
│  (enrichment)   │ ◄── Spy Dialer, etc. → back into pipeline (post-verify merge TBD)
└─────────────────┘
```

---

## 3. Project parts (phases)

| # | Part | Description | Status |
|---|------|-------------|--------|
| **1.1** | Django project setup | Base project, DB, admin | ✅ Done |
| **1.2** | Campaigns | Create/name campaigns (niche, location, notes) | ✅ Done |
| **1.3** | Outscraper file upload | CSV + XLSX import, parse columns/rows | ✅ Done |
| **1.4** | Outscraper filter tags | Category, location, max results, services, extra tags | ✅ Done |
| **1.5** | Import history | Save every original file; list + download | ✅ Done |
| **1.6** | Duplicate filter warning | Same filters → warn; use existing or upload anyway | ✅ Done |
| **1.7** | Column cleaning | Per-campaign column picker; cleaned CSV export | ✅ Done |
| **1.8** | MillionVerifier split | Upload MV result → separate CSVs (good, risky, unknown, …) | ✅ Done |
| **1.9** | UI (basic) | Campaign list, detail, import detail, history page | ✅ Done |
| **1.10** | Verification ZIP | Single `.zip` of all MillionVerifier split CSVs per import | ✅ Done |
| **1.11** | Diana handoff CSV | Rows missing email and/or phone on full Outscraper file + `diana_reason` | ✅ Done |
| **1.12** | **Automatic campaign mode** | On create: choose Automatic → upload → preset columns cleaned → results downloads | ✅ Done |
| **1.13** | **Manual campaign mode** | On create: choose Manual → existing flow (filters, column picker, MV upload) | ✅ Done |
| **2.1** | MillionVerifier API (auto campaigns) | Env key + UI “pending”; bulk auto-upload when key ready | 🔄 Partial |
| **2.2** | Outscraper API | Trigger scrape from app with same filter fields | ⬜ Not started |
| **2.3** | Diana queue (extended) | Merge MV “non-good” + Diana sent/received workflow | ⬜ Not started |
| **2.4** | Phone cleaning path | Dedicated phone column workflow | ⬜ Not started |
| **2.5** | Real Phone Validation | Upload/split by line type (cell vs landline) | ⬜ Not started |
| **3.1** | User accounts & roles | Login; roles (admin, operator, Diana) | ⬜ Not started |
| **3.2** | Search & filter history | Campaign, category/location substring, search `q` | ✅ Done |
| **3.3** | Cost / export tracking | Log Outscraper cost estimate per import | ⬜ Not started |
| **4.1** | Smartlead integration | Upload leads; campaign metadata | ⬜ Not started |
| **4.2** | SimpleTexting integration | Upload SMS contact lists | ⬜ Not started |
| **4.3** | Reporting | Verification rates, campaign stats | ⬜ Not started |
| **4.4** | Owner matching (optional) | NY license / other sources — research phase | ⬜ Not started |

**Progress:** **14 / 26** milestones **done**, **2.1 partial** (~54% of numbered milestones; Phase 4 optional)

---

## 4. How we are building it (approach)

### Phase 1 — Foundation + shipping extras ✅

**Goal:** Replace manual CSV handling and duplicate Outscraper exports.

| Step | How |
|------|-----|
| Import | User uploads Outscraper export; app parses with pandas (CSV/XLSX). |
| Tags | User enters same filters used in Outscraper; stored on `DataImport` + fingerprint hash. |
| Duplicates | SHA-256 fingerprint of category + location + max + services + tags; match → warning page. |
| History | Original file on disk (`media/imports/`); global **Import history** page **with filters** (campaign / category / location / `q`). |
| Clean | User checks columns to keep; app outputs trimmed CSV (`media/cleaned/`). |
| Verify split | User uploads MillionVerifier result; app detects status column and splits into category files. |
| Verify ZIP | **Download all splits** as `verification_splits_…zip` from import detail after split completes. |
| Diana handoff | **Download Diana queue** CSV — full rows where `email_1–3` and/or phone fields are empty (`diana_reason`). |
| **Automatic vs manual** | At **campaign create**, user picks mode. **Automatic:** upload → preset columns → results page. **Manual:** unchanged step-by-step flow. |

**Env (integration prep):**

- `MILLIONVERIFIER_API_KEY` — realtime single-email API helper in `pipeline/services/millionverifier_api.py` ([docs](https://developer.millionverifier.com/)); UI + bulk flows still pending.
- `OUTSCRAPER_API_KEY` — reserved for Outscraper API.

**Tech:** Django 6, SQLite, pandas, openpyxl, **requests**, Bootstrap UI.

---

### Phase 2 — APIs & Diana (extended scope) 🔄 / ⬜

**Goal:** Less manual upload; automatic “missing leads” handoff and bulk verify.

| Step | How |
|------|-----|
| MillionVerifier API | ✅ Realtime helper; ⬜ **Bulk** POST job + webhook/poll matching current split UX |
| Outscraper API | Form submits filters → API job → webhook/poll → auto-create `DataImport`. |
| Diana queue extended | Combine MV **non-good** exports with missing-contact heuristic; Diana status fields |
| Phone path | Second clean template + Real Phone Validation upload/split (mirror email flow). |

**Dependencies:** Client API keys, API docs, sample MV/Outscraper responses.

---

### Phase 3 — Team & operations 🔄 / ⬜

**Goal:** Multiple users; cost tracking.

| Step | How |
|------|-----|
| Auth | Django auth; optional `role` on profile. |
| Search | ✅ Import history filtering (campaign, category substring, location substring, free-text `q`). Optional next: server-side date range. |
| Cost field | Optional `estimated_cost` on import when max_results known. |

---

### Phase 4 — Campaign tools ⬜

**Goal:** Push verified lists into Smartlead & SimpleTexting without re-exporting manually.

| Step | How |
|------|-----|
| Smartlead | API or CSV template export matching their campaign upload format. |
| SimpleTexting | Cell-only CSV export + optional API. |
| Reporting | Dashboard: imports per month, verify pass rate, duplicate saves. |

---

## 5. What is done today (detail)

### App structure

```
Data Management tool/
├── datamanagement/          # Django project settings
├── pipeline/                 # Main app
│   ├── models.py             # Campaign, DataImport, CleanedDataset, Verification*
│   ├── views.py              # Upload, clean, verify, history
│   ├── forms.py
│   ├── services/
│   │   ├── importer.py            # Parse CSV/XLSX
│   │   ├── cleaner.py             # Column subset export
│   │   ├── filters.py             # Fingerprint + duplicate detection
│   │   ├── millionverifier.py     # Split MV result by status
│   │   ├── diana.py               # Diana handoff CSV builder
│   │   └── millionverifier_api.py # MV realtime GET (bulk TBD)
│   └── templates/pipeline/
├── media/                    # Uploaded & generated files
├── PROJECT_PLAN.md           # This file
└── transcript.txt            # Client meeting notes
```

### User flow (live now)

**Create campaign** → choose **Automatic** or **Manual**.

**Automatic mode**

1. Set campaign name, niche, location (used for duplicate tracking).  
2. Upload Outscraper CSV/XLSX → click **Next — process automatically**.  
3. System cleans with **preset columns** (see `pipeline/constants.py`).  
4. **Results** page: download cleaned CSV, Diana queue, original file.  
5. **MillionVerifier:** shows “pending API key” until `MILLIONVERIFIER_API_KEY` is set; then bulk auto-verify will be wired here.

**Manual mode** (unchanged)

1. Enter **Outscraper filters** + upload file.  
2. If duplicate filters → warning → cancel or continue.  
3. **Select columns** → download cleaned CSV.  
4. Upload MillionVerifier result → download splits or ZIP.  
5. Diana queue + import history as before.

**Configuration:** `export MILLIONVERIFIER_API_KEY=your_key` before `runserver` when the client provides it.


### Run locally

```bash
cd "/home/abubakar/Documents/Projects/abdullah/Data Management tool"
source venv/bin/activate
python manage.py runserver
```

Open: http://127.0.0.1:8000/

---

## 6. What is NOT done (client still needs)

| Item | From transcript / client |
|------|--------------------------|
| Outscraper API | “In future we will attach API” |
| MillionVerifier — **bulk** | Realtime helper in codebase; manual MV site upload unchanged until bulk job wired |
| Real Phone Validation | Salvador’s SMS path |
| SimpleTexting | SMS campaigns |
| Smartlead | Email campaigns |
| Diana — **extended** | v1 heuristic export done (missing email/phone). Still need: MV non-good merge + Diana assignment status (~50% post-verify transcript) |
| Owner phone vs business phone | Known gap; no solution yet |
| Monday.com / reply tracking | Matt/Claudia workflow — out of scope for v1 |
| Multi-user login | Not implemented |

---

## 7. Recommended build order (next sprints)

| Sprint | Deliverable | Why |
|--------|-------------|-----|
| **Sprint A (cont.)** | **MV bulk/job API** wire-up + UI “Verify from server” | Finishes MVP email automation |
| **Sprint B** | Extend Diana (+ MV non-good ∪ missing; optional status model) | Transcript fidelity |
| **Sprint C** | Phone path + Real Phone Validation | Parity with email workflow |
| **Sprint D** | Outscraper API | Stop manual download entirely |
| **Sprint E** | Auth + date-range history filter | Team-ready polish |
| **Sprint F** | Smartlead / SimpleTexting | Campaign automation |

**Done vs plan:** ✅ ZIP bundles · ✅ Diana v1 export · ✅ History search/filters *(moved Sprint E partially forward)*  

---

## 8. Open questions for client sign-off

1. Is **email-only** acceptable for next delivery, or must **phone + Diana** ship together?  
2. Do you have **Outscraper** and **MillionVerifier** API keys?  
3. Should duplicate imports be **blocked** or only **warned** (current: warn + allow)?  
4. Who needs login? (Salvador only vs whole team)  
5. Is **Smartlead/SimpleTexting** in scope for this contract or a later phase?  

---

## 9. Progress log

| Date | Update |
|------|--------|
| May 15, 2026 | Django project created |
| May 15, 2026 | Phase 1.1–1.9 complete: upload, tags, history, duplicates, clean, MV split, UI |
| May 15, 2026 | Phase **1.10** ZIP · **1.11** Diana handoff CSV (`diana_reason`) · **3.2** history filters (+ `campaign_selected`) |
| May 15, 2026 | **2.1 partial**: `millionverifier_api.verify_email_realtime`; `settings`: `MILLIONVERIFIER_API_KEY`, `OUTSCRAPER_API_KEY`; dependency **requests** |
| May 15, 2026 | New routes: `/imports/<pk>/download/verification-all.zip`, `/imports/<pk>/download/diana/` |
| May 15, 2026 | **1.12–1.13** Campaign `processing_mode` (automatic \| manual); automatic preset columns in `pipeline/constants.py`; `/imports/<pk>/results/` |

---

*Update the **Status** column in Section 3 and the **Progress log** when each part is completed.*
