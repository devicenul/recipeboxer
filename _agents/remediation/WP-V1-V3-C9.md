# Work Package Completion Report: WP-V1, WP-V3, WP-C9

**Package Owners:** WP-V1, WP-V3, WP-C9  
**Branch:** `remediation/phase0`  
**Status:** Completed & Verified  

---

## 1. Summary of Changes

### WP-V1: Ground-Truth Vault Tooling (`evals/vault.py`)
- **Multi-Recipient age Encryption (`pyrage`)**:
  - Implemented core API and CLI in `evals/vault.py` outside `recipeboxer/`.
  - Commands supported:
    - `keygen [--out PATH]`: Generates an age identity (default `~/.config/recipeboxer/age.key`, mode `600`), strictly refusing paths inside the repository, and printing the public key.
    - `encrypt <in> <out.enc>`: Encrypts files to all authorized recipients in `evals/vault/recipients.txt` (supporting `#` comments).
    - `decrypt <in.enc> [out|-]`: Decrypts age files using identity from `RB_EVAL_AGE_KEY_FILE` or default path, strictly refusing identities inside the repo, with stdout (`-`) and file output options.
    - `encrypt_bytes` & `decrypt_bytes`: In-memory encryption/decryption helpers ensuring ground truth plaintext never touches disk.
    - `rekey <dir>`: Re-encrypts all `*.enc` files in a directory to current recipients entirely in-memory.
    - `add-recipient <pubkey>`: Validates Bech32 `age1...` format and appends to `recipients.txt` without duplicates.
- **Architectural Guardrails (INV-AC1)**:
  - Runtime stack inspection (`_check_import_guard`) checks `inspect.stack()` frame `__name__` attributes and immediately raises `RuntimeError` if imported or called from `recipeboxer` or `recipeboxer.*`.
  - Statically complemented by `tests/anticheat/test_static_invariants.py`.
  - Path safety check (`_ensure_path_outside_repo`) prevents loading or writing private keys inside the repository workspace.

### WP-V3: Multi-Machine Setup & Contributing Guidelines
- **`evals/vault/README.md`**: Comprehensive documentation explaining vault structure (`<tier>.gt.enc`, `recipients.txt`), multi-machine setup (one private identity per machine in `~/.config/recipeboxer/age.key`, registering public key and rekeying), GitHub Actions CI identity setup, and the prohibition against committing identities.
- **`evals/vault/recipients.txt`**: Created with explanatory header comments only (no initial keys).
- **`CONTRIBUTING.md`**: Created top-level guide covering:
  - Dev setup pointers and cache redirection rules (`$HOME/.cache/...`).
  - Test tiers: `offline` (default unit/integration), `live` (LLM inference with spend caps), `score` (ground-truth evaluation with vault identity).
  - Anti-cheat invariants summary (INV-AC1 to INV-AC6) linking to `_agents/INVARIANTS.md`.
  - Contributor execution scope: full T0 access, local T1 image pipeline execution with personal Gemini key, no local T1-T4 scoring.
  - Official scoring via CI two-workflow split (`eval-run.yml` for untrusted PR code, `eval-score.yml` for trusted `main` scoring).
  - Explicit warning never to use `pull_request_target` for untrusted PR code.

### WP-C9: Synthetic Corpus Provenance Notice (`tests/fixtures/sample_cards/README.md`)
- Added prominent notice at the top of the document:
  - All 48 images are synthetic Pillow renders produced by the card generator from recipe text.
  - Historical source references describe recipe text inspiration and historical context, not image scans.
  - Designated as Tier T0 (training/visible), strictly prohibited from quality or benchmark evaluation claims.
- Reworded false claims that the dataset contains digitized physical scans.

---

## 2. Test Verification

Test suite `tests/test_wp_v1_vault.py` was executed with no caches created in the workspace:

```bash
PROJECT_CACHE_DIR="$HOME/.cache/projects/$(basename "$PWD")-$(pwd -P | sha1sum | cut -c1-8)"
python="$PROJECT_CACHE_DIR/venv/bin/python"
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" $python -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_v1_vault.py -q
```

**Results:**
```
.......                                                                  [100%]
7 passed in 0.43s
```

All 7 test scenarios passed:
1. `test_vault_roundtrip_two_recipients`: Multiple recipients decrypt independently both in-memory and on disk.
2. `test_rekey_after_removing_recipient`: Rekeying after recipient removal prevents the revoked key from decrypting.
3. `test_refuse_identity_path_inside_repo`: In-repo keygen/load/decrypt paths trigger `ValueError` (INV-AC1).
4. `test_import_guard_raises_when_called_from_recipeboxer_module`: Execution inside `recipeboxer.*` namespaces raises `RuntimeError` (INV-AC1).
5. `test_add_recipient_validation`: Strict format validation for Bech32 `age1...` public keys.
6. `test_clear_error_messages_when_identity_missing`: Clear, actionable error messages on missing or invalid keys.
7. `test_cli_interface`: CLI commands (`keygen`, `add-recipient`, `encrypt`, `decrypt`) function correctly via argv dispatch.

CLI help verification:
- `$python -m evals.vault --help` exited 0.
- `$python evals/vault.py --help` exited 0.

---

## 3. Owned Files Manifest

- `evals/vault.py` (New)
- `evals/vault/README.md` (New)
- `evals/vault/recipients.txt` (New)
- `CONTRIBUTING.md` (New)
- `tests/fixtures/sample_cards/README.md` (Updated)
- `tests/test_wp_v1_vault.py` (New)
- `_agents/remediation/WP-V1-V3-C9.md` (New)
