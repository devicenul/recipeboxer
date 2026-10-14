# RecipeBoxer Ground-Truth Vault

The Ground-Truth Vault securely manages encrypted ground-truth datasets for RecipeBoxer evaluation tiers (T1 through T4) using multi-recipient [age encryption](https://github.com/FiloSottile/age) (via `pyrage`).

Production code (`recipeboxer/`) is architecturally isolated from the vault and has no access to ground-truth data or keys (enforcing Invariant **INV-AC1**).

---

## 1. Vault Layout

The vault directory structure contains encrypted ground truth and recipient public keys:

```
evals/vault/
├── README.md               # This specification and operations guide
├── recipients.txt          # Public age keys of authorized maintainers and CI
├── t1.gt.enc               # Tier 1 (dev-eval) encrypted ground-truth archive
├── t2.gt.enc               # Tier 2 (held-out gate benchmark) encrypted archive
├── t3.gt.enc               # Tier 3 (real-web transcribed cards) encrypted archive
└── t4.gt.enc               # Tier 4 (real-user private photo sets) encrypted archive
```

- **`recipients.txt`**: Contains one public key per line (format: `age1...`). Comments starting with `#` and blank lines are ignored.
- **`<tier>.gt.enc`**: Binary age-encrypted archives. Each file is encrypted simultaneously to all public keys listed in `recipients.txt`. Any single authorized private identity can decrypt the vault.
- **In-Memory Operations**: Decryption and rekeying operate strictly in-memory (`encrypt_bytes` and `decrypt_bytes`). Plaintext ground truth is never written to disk in the workspace.

---

## 2. Multi-Machine Setup

Maintainers working across multiple development machines or environments (e.g. laptop, workstation, cloud VM) maintain **one distinct age identity per machine**.

### Step 1: Generate an Identity on the Machine

On the local machine, generate an identity outside the workspace:

```bash
python -m evals.vault keygen
```

By default, this writes the identity key to:
```
~/.config/recipeboxer/age.key
```
with POSIX permissions `600` (`-rw-------`).

The CLI outputs your public key:
```
Public key: age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p
Identity key written to: /home/user/.config/recipeboxer/age.key (mode 600)
```

Alternatively, specify an explicit location outside the repository:
```bash
python -m evals.vault keygen --out ~/.keys/recipeboxer_age.key
```

### Step 2: Add Public Key to Recipients

Add the newly generated public key to `evals/vault/recipients.txt`:

```bash
python -m evals.vault add-recipient age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p
```

### Step 3: Rekey the Vault

Re-encrypt existing `.enc` files so the new machine's identity can decrypt them:

```bash
python -m evals.vault rekey evals/vault
```

Rekeying decrypts each archive in-memory using an existing authorized identity and immediately encrypts it to all public keys in `recipients.txt`.

---

## 3. CI Identity & Automated Evaluation

Official benchmark scoring runs in GitHub Actions using a dedicated CI identity.

1. **Secret Storage**: The CI private identity is stored strictly in GitHub Actions repository secrets as `AGE_CI_IDENTITY` (or configured via environment variable `RB_EVAL_AGE_KEY_FILE`).
2. **Public Key**: The corresponding public key is registered in `evals/vault/recipients.txt`.
3. **Workflow Isolation**:
   - Untrusted pull request code runs in `eval-run.yml` with **NO vault secrets**.
   - Trusted scoring runs in `eval-score.yml` triggered via `workflow_run`, checking out trusted code from `main`, mounting the CI identity, and computing aggregate metrics.

---

## 4. Security Guardrails & Invariants

- **NEVER Commit Private Identities**:
  - `AGE-SECRET-KEY-...` strings must **NEVER** be committed to git, placed in the workspace, or synced across shared directories.
  - `.gitignore` explicitly ignores identity files, but `evals/vault.py` also enforces this programmatically: any attempt to specify a key path inside the repository triggers a `ValueError` security violation.
- **Invariant INV-AC1**:
  - No module under `recipeboxer/` may import or call `evals.vault`.
  - Dynamic stack inspection in `evals/vault.py` raises `RuntimeError` if imported or called from `recipeboxer.*`.
  - Static AST scanners in `tests/anticheat/test_static_invariants.py` continuously enforce this boundary.
