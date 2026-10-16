# Archived tier T3A: antique manuscript recipes (opt-in only)

**Status: ARCHIVED / deactivated (WP-T3A, 2026-10-16). Not part of any default or all-tier run.**

This folder holds the corpus that used to be tier **T3**: 25 real 17th-century English manuscript
recipes (26 page images, deck `d01`) from the Folger Shakespeare Library / Wellcome Collection,
human-transcribed on FromThePage. It was deactivated because RecipeBoxer targets recipes from the
**1900s onward**. Tier **T3** is now the real-world *modern* (1900s+) tier, pending curation (see
[`evals/INTAKE.md`](../../../INTAKE.md)).

| Item | Location |
|---|---|
| Images + public deck list | `evals/corpora/archive/t3_antique/d01/`, `DECKS.json` |
| Provenance / licences | `ATTRIBUTION.md` (per opaque image id), `CANDIDATES.md` (original candidate notes) |
| Encrypted GT | `evals/vault/archive/t3a.gt.enc` (the GT JSON still carries the legacy label `"tier": "T3"`) |
| Frozen fetch specs | `evals/curation_t3/specs_t3a_antique.py` (`fetch --preset t3a_antique --tier T3A`) |

## Why it is kept

The `honest_v0` / `honest_v1` baselines' "T3" numbers were measured on this set, and their SUT output
archives are labelled `T3`. They stay re-scorable with the explicit opt-in tier name:

```bash
python -m evals.score --tier T3A --outputs <archived honest_v1 T3 outputs dir> --report <dir>
python -m evals.harness.run --tier T3A --out <dir>      # re-run (needs a live key; costs quota)
```

The scorer relabels legacy `T3` outputs as `T3A` in the report. Feedback policy is the same as for
T1–T4 (aggregate only).

## GT review status

Structured GT is `drafted_unreviewed`. The planned 20% human spot-check was **waived** by the user on
2026-10-16: the user looked at the spot-check package, judged the decoding "quite good overall", and
declined to give line-level corrections because the antique set is out of product scope.
Antique-manuscript handling stays an optional, low-priority use case (see `docs/ROADMAP.md`).
