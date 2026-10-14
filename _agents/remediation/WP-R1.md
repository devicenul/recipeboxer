# WP-R1: Real-Web Candidate Corpus Discovery (T3 Evaluation Tier)

## 1. Summary

Work Package **WP-R1** implements candidate discovery for the **T3 "real-web"** evaluation tier of RecipeBoxer, following the specifications in `_agents/remediation/REMEDIATION_PLAN.md` §3.3.

The deliverable inventory is committed at `evals/corpora/real_web/CANDIDATES.md`, documenting **48 candidate real photographed and scanned recipe documents** across national libraries, university archives, and open repositories.

---

## 2. Invariants & Ground Truth Secrecy (INV-AC3)

In accordance with **Invariant INV-AC3** and §3.2–§3.3 of the Remediation Plan:
- **Zero ground-truth leakage:** No verbatim transcription text, recipe instructions, ingredient names, or quantities are recorded in any tracked file in the repository.
- **Neutral identification:** Specific recipe titles that could disclose answers to the LLM or trigger test-time contamination are strictly excluded. All candidate items are identified solely through institutional catalog identifiers, opaque IDs (`rw-001` .. `rw-048`), and neutral descriptors (e.g., `"handwritten cake recipe card, c.1950s"`, `"17th-century manuscript household receipt page, 1651"`).
- **Public metadata only:** Only institutional repository URLs, image endpoints, IIIF manifest links, rights statements, and document characteristics are stored.

---

## 3. Sourcing Strategy & Institutional Targets

Candidates were discovered and verified across seven primary archival domains:

1. **Smithsonian Institution (transcription.si.edu, aaa.si.edu, sova.si.edu):**
   - *Status:* Smithsonian Open Access assets released under **Creative Commons Zero (`CC0`)**.
   - *Findings:* Uncovered mid-century commercial recipe cards from the National Museum of American History Archives Center (NMAH Cookbook Collection, AC.0510) and artist recipe notebooks/cards from the Archives of American Art (Cleo Dorman, Dorr Bothwell).
2. **Library of Congress "By the People" & Manuscript Division (loc.gov & crowd.loc.gov):**
   - *Status:* Public domain (`PD` / "No known restrictions on publication").
   - *Findings:* 18th- and 19th-century manuscript recipe slips (Jefferson papers, Washington correspondence, regional folklife survey index cards). Verified that crowdsourced transcriptions completed under the *By the People* campaign (`gdccrowd`) are mirrored directly into the Library of Congress digital storage repository (`tile.loc.gov/storage-services/service/gdc/gdccrowd/...`).
   - *Memorization Probe Control:* The famous Rosa Parks pancake recipe card (`rw-044`, jotted on the back of a Detroit bank deposit envelope) was located with its IIIF manifest and verified volunteer transcription; flagged as **High Fame Risk / Unclear Rights** for use as a memorization benchmark rather than a standard extraction test.
3. **Wikimedia Commons (`Category:Recipe cards`):**
   - *Status:* Permissive licenses (`CC-BY-SA 4.0`, `CC-BY-SA 3.0`, `Public domain`).
   - *Findings:* Real photographed mid-20th-century family recipe cards (e.g. Michigan c. 1950s 3x5 ruled index cards from private estates). These represent the closest physical analog to authentic user photos, featuring fountain pen ink, ruled lines, aging, and stains.
4. **FromThePage Manuscript Cookbooks Collaboration:**
   - *Status:* Public Domain Mark (`PD` / PDM) and institutional research open access.
   - *Institutions:* Folger Shakespeare Library, Wellcome Collection, Royal College of Physicians, University of Guelph, University of Pennsylvania Kislak Center, and Kansas State University Libraries.
   - *Findings:* 100% human-transcribed manuscript recipe books with full IIIF manifests (`iiif.wellcomecollection.org`) and high-resolution JPEG streams.
5. **MSU Libraries Feeding America Project (d.lib.msu.edu/fa):**
   - *Status:* Public Domain Mark 1.0 (`PD`).
   - *Findings:* Digitized early 20th-century promotional booklets and 19th-century American cookbooks with complete TEI XML and plaintext human transcriptions.
6. **NYPL Digital Collections (Whitney Cookery Collection):**
   - *Status:* Public Domain (`PD`).
   - *Findings:* 17th-century English manuscript receipt books (e.g., Joane Yate Her Booke, 1682).
7. **National Archives and Records Administration (NARA):**
   - *Status:* Public Domain (`PD`, U.S. Government work).
   - *Findings:* Official White House printed and typed recipe cards (LBJ Presidential Library).

---

## 4. Verification Methodology

Every candidate was verified for:
1. **Rights and Licensing:** Verified presence of an explicit rights statement: CC0, Public Domain / No Known Restrictions, CC-BY-SA, or flagged as Unclear.
2. **Image Download Availability:** Verified direct high-resolution image endpoints (direct JPEG/PNG/GIF) or IIIF Presentation API v2/v3 manifests (`iiif.wellcomecollection.org`, `tile.loc.gov/image-services/iiif/`, `colenda.library.upenn.edu`).
3. **Presence of Human Ground Truth:** Verified existence of a complete, human-produced transcription URL (FromThePage volunteer transcription runs, Library of Congress `gdccrowd` text files, Founders Online / Monticello scholarly transcripts, or MSU TEI full text). For items lacking existing transcripts (Wikimedia Commons cards), marked explicitly as needing manual transcription in WP-R2.
4. **Physical Dimensions & Pagination:** Verified single-page vs. multi-page status, card layout, and front/back pairing opportunities.
5. **Legibility & Model Fame Risk:** Evaluated script style, contrast, and potential risk of model memorization (flagging famous artifacts as High Fame Risk).

---

## 5. Candidate Distribution Summary

- **Total Candidates:** 48 items
- **By License Class:**
  - Public Domain (`PD` / PDM): 38 items (79.2%)
  - Creative Commons Zero (`CC0`): 3 items (6.2%)
  - Creative Commons Attribution-ShareAlike (`CC-BY-SA`): 6 items (12.5%)
  - Unclear Rights: 1 item (2.1%, Rosa Parks envelope; retained for memorization audit only)
- **By Document Format:**
  - Handwritten Recipe Cards & Envelope: 7 items (14.6%)
  - Typed / Printed Cards: 4 items (8.3%)
  - Manuscript Recipe Pages & Slips: 34 items (70.8%)
  - Booklets / Clippings / Printed Pages: 3 items (6.3%)
- **Multi-Page & Front/Back Physical Pairs:**
  - `rw-044`: True 2-sided envelope (front bank address/postmark, back handwritten recipe).
  - `rw-016` / `rw-017`: Consecutive facing manuscript folios (Lady Ann Fanshawe 1651, f. 4r and 4v) forming a continuous recipe sequence.
  - Multi-page sequences available across 100%-transcribed FromThePage manuscript collections.

---

## 6. Recommended Top 30 for T3 Curation

Remediation Plan §3.3 calls for curating a 30-card real-world evaluation tier. The recommended 30 items (`rw-001`–`rw-006`, `rw-008`–`rw-011`, `rw-016`–`rw-019`, `rw-021`–`rw-024`, `rw-027`–`rw-033`, `rw-035`, `rw-037`, `rw-040`–`rw-042`) provide:
- **Format balance:** 6 handwritten index cards, 3 typed/printed cards, 19 manuscript pages, 2 printed/booklet pages.
- **Stationery testing:** Homogeneous card stocks (e.g. `rw-001` and `rw-002` from the same 1950s box) to test physical similarity weighting without false merging.
- **Zero memorization bias:** Obscure domestic receipts prioritizing low fame risk. Famous historical recipes (`rw-044`, `rw-045`, `rw-046`) are sequestered as controls for the memorization probe.

---

## 7. Gaps & Next Steps for WP-R2

1. **Card Transcription:** The 5 authentic 1950s recipe cards from Wikimedia Commons (`rw-001`–`rw-005`) provide the most realistic real-world cards but lack existing transcripts. The Eval Curator in WP-R2 must produce a verified verbatim transcription pass for these cards before vault encryption.
2. **Two-Sided Card Rarity:** Standard public domain library archives overwhelmingly scan single sides or bound codices. Multi-card grouping evaluation on real user photos will be fully addressed in **T4** (user photo intake with `evals/intake_real.py`).
3. **Execution Plan for WP-R2:**
   - Run the memorization probe script against `rw-044`–`rw-046` and selected candidates using prompt completion on recipe titles.
   - Download full-resolution image assets and normalize EXIF orientations.
   - Assign opaque random identifiers (`t3_card_xxxxxxxx.jpg`).
   - Format structured ground truth into Schema v2 format and encrypt directly into `evals/vault/t3.gt.enc` with `RB_EVAL_AGE_KEY_FILE`. Plaintext GT must never touch disk.
