# WP-RM — Roadmap Merge Report

**Date:** 2026-10-16  
**Author:** WP-RM Agent  
**Branch:** `remediation/phase0`  
**Base HEAD:** `9d45470`  
**Mode:** Docs-only. Zero code modifications. Zero state-mutating git operations. `setup.sh` untouched. No API keys accessed.

---

## 1. Summary of Actions

Following user approval to merge the two roadmap documents, **`docs/ROADMAP.md`** has been established as the single canonical roadmap and prioritized backlog for RecipeBoxer.

### Modified Files:
1. **`docs/ROADMAP.md`** (Updated canonical roadmap):
   - **Completed Milestones**:
     - Carried over M1–M16 product milestone table concisely with explicit `Superseded:` notes where early claims or implementations were superseded or identified as eval cheats by the October 2026 remediation (e.g. greedy bipartite matching $\rightarrow$ `path_cover` DAG correlation with `DeckBackground` likelihood ratios; Gemini 3.7/2.5 $\rightarrow$ `gemini-3.8-flash` / `gemini-3.1-pro` defaults; initial synthetic corpus demoted to T0 train; legacy eval harness, mock judge 4.96/5.0, and manifest hash lookups excised; test count adjustments).
     - Added comprehensive **Eval-Cheat Remediation (2026-10)** entry covering Phases 0–6, Gate A certification (`honest_v0.json` on 2026-10-15), Phase 5 red-team challenger audits (#2, #3, `WP-RT3FIX` SUT sandbox isolation & `ece_all`), and Gate B certification (`honest_v1.json`, tagged `gate-b`).
   - **Gate B Certification & Done Criteria**:
     - Detailed certification of all 4 criteria: Tier T2 held-out generalization (B-Cubed F1 `0.9210`, Kendall $\tau = 1.0000$), homogeneous scaling on 319-card deck `d04` ($O(N \cdot k)$ with 1,086 calls and zero false merges), Red-Team challenger #3 clean audit (100% canaries killed, strict sandbox, headline `ece_all`), and baseline committed.
   - **Prioritized Remaining Work**:
     - Prioritized backlog table and detailed deep dives:
       - **P1 (Quality)**: `d04` Same-Stock False Split Reduction (0.5664 false split; SetVerifier back $\rightarrow$ next-front rejection reduction via semantic continuity cues in `set_verify_v2.md` and score biasing in `engine.py`).
       - **P1 (Extraction)**: Tier T3 Real-Manuscript Extraction & Title CER (0.4840 matched / 0.5665 e2e; 18th-century manuscript hands and multi-recipe archival pages).
       - **P2 (Quality & Reliability)**: Tier T3 Recipe Calibration (`ece_all` 0.2844) & `INVALID_RESPONSE` Resiliency (handling rare empty model responses during complex script transcription; tuning paleography prompts).
       - **P2 (Test Hygiene)**: Remediation of 14 known platform/environmental test failures (6 Windows zip-slip tests, 7 headless Tkinter tests, 1 environmental `setup.sh` python discovery test).
       - **P3 (Architecture)**: CardForge standalone repository split (Decision D8).
       - **P3 (Cloud Integration)**: Official Google Cloud OAuth Provisioning & Fallback Deprecation (Phase 10: dedicated zero-cost GCP project, OAuth 2.0 Web Client for Docs/Drive scopes, embed public Client ID, deprecate manual token drawer).
   - **Deferred: Maintainer Setup (User-Owned, Out of Current Scope)**:
     - Fully self-contained instructions for provisioning repository secrets and multi-recipient `age` encryption keys without needing historical conversation context:
       - Architecture of the two-workflow split (`eval-run.yml` untrusted PR execution with `CI_GEMINI_API_KEY` vs. `eval-score.yml` trusted `main` branch execution with `CI_AGE_IDENTITY`).
       - Step-by-step instructions: keypair generation (`python -m evals.vault keygen`), adding recipients (`evals.vault add-recipient` & `recipients.txt`), vault rekeying (`evals.vault rekey`), secret storage in GitHub Actions, and `eval-live` environment creation.
       - Direct link to `docs/CI.md`.
   - **Waiting on User Input**:
     - **T3 Ground-Truth 20% Spot-Check**: 5 recipes (`r001`, `r004`, `r009`, `r021`, `r024`) with review package in maintainer's Antigravity artifact folder; corrections applied to `evals/vault/t3.gt.enc` via `evals.vault` and structured status set to `reviewed`.
     - **T4 Real User Photo Intake**: Ingestion guide in `evals/INTAKE.md`, natural kitchen photography, GPS stripping, encrypted vault storage, and paid Gemini API key recommendation for family photo privacy.
   - **Hyperlink Hygiene**: Strictly relative markdown links (no `file://` absolute paths, no broken relative directory paths).
2. **`ROADMAP.md`** (Root file):
   - Replaced with a 3-line pointer redirecting users and existing links to `docs/ROADMAP.md`.
3. **`README.md`**:
   - Updated roadmap references under the documentation index and Developer & Agent Context Hub to designate `docs/ROADMAP.md` as the canonical roadmap and note that root `ROADMAP.md` is a pointer.
4. **`_agents/README.md`**:
   - Updated documentation index table, repository directory map, and Rule 5 (Roadmap Discipline) to point to `docs/ROADMAP.md`.

---

## 2. Verification

- Read-only git status confirms only documentation files were updated (`docs/ROADMAP.md`, `ROADMAP.md`, `README.md`, `_agents/README.md`, `_agents/remediation/WP-RM.md`).
- `setup.sh` was completely untouched.
- No Python code or frontend code was modified.
- No state-mutating git operations were executed.
