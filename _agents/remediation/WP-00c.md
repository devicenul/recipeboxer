# WP-00c: Invariants & Testing Guidelines Remediation

- **Work Package**: WP-00c
- **Plan Reference**: `_agents/remediation/REMEDIATION_PLAN.md` (§4.1, §4.2, §3.4, §6.1, §10)
- **Status**: Complete
- **Date**: 2026-10-14
- **Branch**: `remediation/phase0`

---

## 1. Executive Summary

Work Package WP-00c establishes the formal architectural invariants and testing standards required to eliminate evaluation cheats across the RecipeBoxer codebase. This documentation ensures all future agent swarm waves and human contributors adhere strictly to non-cheating evaluation policies:
1. Ground truth is strictly segregated into an encrypted vault and decrypted exclusively by the scorer using an external `age` key.
2. Production code and offline tests are prohibited from reading manifests, parsing filename tokens, or embedding canned recipe text.
3. Offline tests assert system behavior only; live model interaction and scoring suites assert accuracy.

---

## 2. Summary of Documentation Changes

### 2.1 `_agents/INVARIANTS.md`
Appended `## 10. Anti-Cheat Invariants (INV-AC1..INV-AC6)` continuing the established numbering style:
- **INV-AC1 (No Answer Key)**: Forbids production code under `recipeboxer/` from reading `tests/`, `evals/`, manifests, vault files, or the scorer identity (`age` key via `RB_EVAL_AGE_KEY_FILE` outside the workspace).
  - *Rationale*: Cites Oct 2026 audit findings on manifest SHA/filename lookups in `extractor.py:125-193,680-770`, `semantic.py:48-71,262-278`, and `router.py:35-58,337-392`.
  - *Enforcement*: `tests/anticheat/test_static_invariants.py`, Phase 1 canary tests, and red-team review.
- **INV-AC2 (No Filename Meaning)**: Mandates that filenames/card IDs are opaque keys; forbids substring/regex heuristics.
  - *Rationale*: Cites Oct 2026 audit findings on filename heuristics in `fingerprint.py:~253-379`, `heuristics.py:512-567`, and `router.py:77-107,399-427`.
  - *Enforcement*: `tests/anticheat/test_static_invariants.py`, rename-invariance canary, and red-team review.
- **INV-AC3 (No Corpus Content)**: Prohibits GT titles, distinctive recipe text, or 64-hex SHA-256 corpus image hashes in production code.
  - *Rationale*: Cites Oct 2026 audit findings on canned recipes (cinnamon rolls, lemon bars, fudge, pecan in `extractor.py:773-901`) and direct SHA lookups.
  - *Enforcement*: `tests/anticheat/test_static_invariants.py` (deny-list scanner), Phase 1 prompt-leak scan canary, and red-team review.
- **INV-AC4 (No Silent Fabrication)**: Requires typed `StageFailure` routed to HITL; prohibits silent fake/canned fallbacks.
  - *Rationale*: Cites Oct 2026 audit findings on silent fallbacks across all three pipeline stages.
  - *Enforcement*: `tests/anticheat/test_static_invariants.py`, failure propagation canary tests, and red-team review.
- **INV-AC5 (Black-Box Evals)**: Requires SUT to run in scrubbed subprocess with runtime audit hook; GT decrypted by scorer after execution.
  - *Rationale*: Cites Oct 2026 audit findings on harness leaks (`obfuscation.py:271`, `runner.py:467-522`, `loader.py:195`).
  - *Enforcement*: `tests/anticheat/test_static_invariants.py`, audit-hook trip canary, poisoned vault canary, and red-team review.
- **INV-AC6 (LLM Required)**: Requires active LLM and API key; no key results in blocked onboarding state; OCR is evidence only, never fallback.
  - *Rationale*: Cites Oct 2026 audit findings on default mock judges and offline extraction bypassing model requirements.
  - *Enforcement*: `tests/anticheat/test_static_invariants.py`, missing-key blocked state canary, and red-team review.

### 2.2 `_agents/TESTING_GUIDELINES.md`
1. Added `## 7. Writing Non-Cheating Tests` covering:
   - **Test Execution Tiers**: `offline` (behavior only, never accuracy), `@pytest.mark.live` (real LLM with budget caps), and `score` (isolated scorer CLI using `age` identity).
   - **Mocking Rules**: Mocks must be keyed strictly by call order or explicit in-test mappings; never by filename or image content hash.
   - **Manifest Isolation**: Forbids production imports of `tests/fixtures/`, `manifest.json`, or eval vault.
   - **Corpus Tiers (T0..T4)**: Defines visibility rules (T0 GT visible; T1 images visible, GT hidden in vault; T2 images/GT hidden; T3 real-web images visible, GT hidden; T4 real-user images per preference, GT hidden).
   - **T1 Feedback Policy**: Aggregate and per-category breakdown only; never per-image diffs; rate-limited ($\le 10$ runs/day).
   - **Good vs. Bad Code Snippets**:
     - *Bad*: names files `apple_crisp_front.jpg`, asserts pairing offline, asserts canned recipe extraction from corpus.
     - *Good*: opaque card IDs, injects `MockGeminiClient` returning explicit ordered responses, asserts sequence optimizer behavior.
   - **Reviewer Checklist**: Concrete checklist for reviewing tests against anti-cheat rules.
2. Annotated conflicting legacy sections with callouts:
   `> [!WARNING] Superseded by Writing Non-Cheating Tests (2026-10)`:
   - Principle 3 (100% Offline Testability).
   - Section 3.2 Core Fixtures (`sample_card_pairs` verified against `manifest.json`).
   - Section 6 Pre-Commit Quality Checklist (mandate for 100% pass rate on contaminated cheat suites).

### 2.3 `_agents/README.md`
- Added link to `remediation/REMEDIATION_PLAN.md` in the Core Documentation Index table.
- Added `remediation/` and `REMEDIATION_PLAN.md` to the Repository Directory Map.

---

## 3. Inventory of Conflicting Guidance Found

The following conflicting instructions and assumptions were identified across the documentation and test harness:

| File | Exact Quote | Conflict Analysis | Resolution / Status |
|---|---|---|---|
| `_agents/TESTING_GUIDELINES.md:15-16` | `"3. **100% Offline Testability**:\nAll test suites run completely offline without an active internet connection or live GEMINI_API_KEY, using high-fidelity offline simulators..."` | Encouraged designing offline test suites that claimed to verify full extraction and correlation accuracy offline, which directly incentivized manifest lookup tables and canned recipe cheats in production code. | Marked with `> [!WARNING] Superseded by Writing Non-Cheating Tests (2026-10)`. Clarified that offline tests assert behavior only. |
| `_agents/TESTING_GUIDELINES.md:96` | `"\| \`sample_card_pairs\` \| \`List[Tuple]\` \| Ground-truth front/back pairs verified against \`manifest.json\`. \|"` | Recommends `manifest.json` as a fixture for testing pairing, violating INV-AC1 and encouraging tests to assert pairing accuracy offline against historical ground truth. | Marked with `> [!WARNING] Superseded by Writing Non-Cheating Tests (2026-10)`. Fixture demoted to T0 behavioral testing only. |
| `_agents/TESTING_GUIDELINES.md:248` | `"- [ ] ./test.sh passes with 1,848 passing tests (0 failures, 0 errors across Tiers 1–5)."` | Mandates a 100% pass rate across legacy test suites, including ~45 tests that directly test or assert cheating mechanisms (manifest lookups, filename heuristics). | Marked with `> [!WARNING] Superseded by Writing Non-Cheating Tests (2026-10)`. Addressed by Phase 2 test triage (WP-C8). |
| `_agents/README.md:98,135-136` | `"The repository maintains 2,205 passing tests (1,848 Python pytest tests + 357 Frontend Vitest tests across 29 suites). Zero test failures or regressions are permitted."` | Enforces zero regressions on suites currently containing contaminated baseline tests that test answer key lookups and filename cheats. | Superseded by Phase 0 WP-00a (`xfail` marking of anti-cheat static tests) and Phase 2 test triage (WP-C8). |
| `_agents/ARCHITECTURE.md:492` | `"- Benchmarks extraction and correlation against the 48-card historical corpus (tests/fixtures/sample_cards/manifest.json)."` | Directs benchmarks to rely on `manifest.json`, the primary vector for contaminated SHA/filename lookups in production modules (`extractor.py`, `semantic.py`, `router.py`). | Demoted to T0 train. Official benchmarks move to T1–T4 via the encrypted vault and `evals/score.py` (WP-V1, WP-E4). |
| `tests/fixtures/sample_cards/README.md:1-20` | Descriptions implying historical scan provenance for the 48 fixture images. | All 48 images are synthetic cards generated from templates in `card_generator.py`. | Flagged for rewrite in Phase 2 under WP-C9. |

---

## 4. Verification & Validation

1. **Non-destructive Modification**:
   - All preexisting invariants (Invariants 1 through 9) in `_agents/INVARIANTS.md` remain intact and unmodified.
   - All preexisting testing guides, Vitest instructions, and CLI command references in `_agents/TESTING_GUIDELINES.md` remain intact.
   - Preexisting documentation links in `_agents/README.md` remain intact.
2. **Git & Code Safety**:
   - No git commands (`git add`, `git commit`, `git checkout`, `git stash`) were executed.
   - `setup.sh` and production code files were left untouched.
3. **Consistency**:
   - The age identity terminology (`RB_EVAL_AGE_KEY_FILE` outside the workspace) matches Plan §3.4 and §6.1.
   - Test tiers (`offline`, `@pytest.mark.live`, `score`) and corpus tiers (`T0`–`T4`) match Plan §3.1, §4.2, and §6.1.
