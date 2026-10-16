# Tier T3: real-world modern recipes (1900s onward)

**Status (WP-T3I, 2026-10-16): imported. Grouping GT is final. Recipe-text GT (verbatim + structured) is
pending and will be drafted when API quota allows.**

T3 is the real-world tier in the product's target era. It holds photos and scans of recipe cards,
letters, notebook and cookbook pages, plus some non-recipe images. All items were openly licensed and
gathered by the curator agent WP-T3M without any LLM calls.

| Item | Location |
|---|---|
| Images (one deck) | `d01/`: 50 opaque, randomly named JPEGs |
| Public deck list | `DECKS.json` (`deck_id` and `n_images` only) |
| Licences and credits | `ATTRIBUTION.md` (opaque filename -> source credit) |
| Encrypted GT | `../../vault/t3.gt.enc` (age; recipients in `../../vault/recipients.txt`) |

## Composition

- **Size:** 50 images and 49 recipe groups. 48 groups are scored. The remaining group is a tail-only
  fragment whose start is missing from the corpus, so it is kept in the GT but excluded from scoring.
  Some images are distractors (letters, receipts, blank pages, a poster, and so on) that must not
  become recipes.
- **Layouts:** single cards, front/back cards, recipes that cross a page break, and pages that carry
  several recipes. The scorer supports images that belong to more than one recipe (see
  `docs/EVALS.md`).
- **Era:** 1900s to 1990s. Groups by era bucket: 1900–1929: 8; 1930–1959: 6; 1960–1999: 8; undated: 26
  (mostly family cards and one manuscript book).
- **Modality (groups):** handwritten 33, printed 7, typed 6, mixed 2.
- **Famous items:** about 17% of groups come from presidential libraries or the White House. These and a
  public-domain community cookbook with OCR online carry `famous` / `memorization_risk` flags in the
  GT, so results can be split by memorization risk.
- **Licences:** public domain (NoC-US / PD-USGov / LOC public-domain books), no known copyright
  restrictions (NKC / Flickr Commons), CC BY 2.0, CC BY 4.0, and CC BY-SA 2.0. For CC BY-SA items,
  derivatives stay under CC BY-SA. Per-image credits are in `ATTRIBUTION.md`.

## Curation process

1. **Discovery and rights check (WP-T3M).** Sources were Wikimedia Commons, Flickr (CC BY / BY-SA only),
   and the Library of Congress. Items were dropped if their rights were unverified, if they were
   third-party photos of post-1928 commercial print, if they were pre-1900, or if they were already
   used elsewhere in the benchmark. Two front/back pairings were inferred from content rather than
   labelled by the uploader; the GT marks them `pairing: inferred`.
2. **Import (WP-T3I).** Each image got a new random id, unrelated to the staging ids. Images were
   downscaled to at most 2400 px on the long side, converted to sRGB JPEG (quality 85), and stripped of
   all EXIF, GPS, XMP, ICC and comment metadata. The originals and the staging-to-repo id map stay
   outside the repo.
3. **GT.** The grouping, reading order, layout, modality, era, famous/memorization flags, source cluster,
   pairing confidence, distractor list and the exclusion of the fragment are encrypted in `t3.gt.enc`.
   Every recipe has `structured_status: pending_draft` and an empty `verbatim`.

## Recipe-text GT (pending)

There are no human transcriptions for this set. When quota allows, the maintainer drafts verbatim and
structured GT from the images with `python -m evals.curation_t3.draft_structured --from-images`
(gemini-3.1-pro-preview, about $2–3), runs `memorization_probe`, and spot-checks at least 20% with
`spotcheck`. The exact commands are in `evals/INTAKE.md` §T3. Until then, T3 runs report grouping
metrics, and extraction metrics are reported as **N/A ("structured GT pending")**, not as zero.
