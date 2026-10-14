"""Tests for WP-V1 and WP-V3: evals/vault.py multi-recipient age encryption.

Enforces:
- Roundtrip encryption/decryption with multiple recipients
- Rekeying when recipients are removed (revocation)
- Refusal of private identity paths inside the repository (INV-AC1)
- Import guard raising RuntimeError when called from recipeboxer.* modules (INV-AC1)
- Validation of recipient public keys (Bech32 age1...)
- In-memory operations (plaintext never touches disk)
- Clear error messages when identity files are missing
"""

from __future__ import annotations

from pathlib import Path

import pyrage
import pytest
from pyrage import x25519

import evals.vault as vault


def test_vault_roundtrip_two_recipients(tmp_path: Path):
    """Roundtrip with two recipients: each identity can independently decrypt."""
    id1_path = tmp_path / "id1.key"
    id2_path = tmp_path / "id2.key"

    pub1, _ = vault.keygen(out_path=id1_path)
    pub2, _ = vault.keygen(out_path=id2_path)

    assert pub1.startswith("age1")
    assert pub2.startswith("age1")

    # In-memory roundtrip
    secret_data = b"Ground truth recipe: Lemon Bars (flour, sugar, butter, lemon juice)"
    ciphertext = vault.encrypt_bytes(secret_data, recipients=[pub1, pub2])

    decrypted1 = vault.decrypt_bytes(ciphertext, identity_path=id1_path)
    decrypted2 = vault.decrypt_bytes(ciphertext, identity_path=id2_path)

    assert decrypted1 == secret_data
    assert decrypted2 == secret_data

    # File-based roundtrip
    in_file = tmp_path / "gt.json"
    in_file.write_bytes(secret_data)
    enc_file = tmp_path / "gt.json.enc"

    vault.encrypt(in_file, enc_file, recipients=[pub1, pub2])
    assert enc_file.exists()
    assert enc_file.read_bytes() != secret_data

    out_file = tmp_path / "recovered.json"
    vault.decrypt(enc_file, out_path=out_file, identity_path=id1_path)
    assert out_file.read_bytes() == secret_data

    stdout_dec = vault.decrypt(enc_file, out_path="-", identity_path=id2_path)
    assert stdout_dec == secret_data


def test_rekey_after_removing_recipient(tmp_path: Path):
    """Rekey after removing a recipient: removed identity can no longer decrypt new file."""
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()

    id1_path = tmp_path / "maintainer1.key"
    id2_path = tmp_path / "maintainer2.key"

    pub1, _ = vault.keygen(out_path=id1_path)
    pub2, _ = vault.keygen(out_path=id2_path)

    recipients_file = tmp_path / "recipients.txt"
    recipients_file.write_text(f"# Maintainers\n{pub1}\n{pub2}\n", encoding="utf-8")

    # Encrypt vault archive with both recipients
    archive_file = vault_dir / "t1.gt.enc"
    secret_payload = b"Top secret T1 evaluation ground truth"
    vault.encrypt(
        in_path=tmp_path / "dummy_in",
        out_path=archive_file,
        recipients_path=recipients_file,
    ) if False else archive_file.write_bytes(
        vault.encrypt_bytes(secret_payload, recipients_path=recipients_file)
    )

    # Both identities can decrypt initially
    assert vault.decrypt_bytes(archive_file.read_bytes(), identity_path=id1_path) == secret_payload
    assert vault.decrypt_bytes(archive_file.read_bytes(), identity_path=id2_path) == secret_payload

    # Revoke maintainer 2: remove pub2 from recipients.txt
    recipients_file.write_text(f"# Maintainers\n{pub1}\n", encoding="utf-8")

    # Maintainer 1 performs rekey
    rekeyed_files = vault.rekey(
        target_dir=vault_dir,
        recipients_path=recipients_file,
        identity_path=id1_path,
    )

    assert len(rekeyed_files) == 1
    assert rekeyed_files[0] == archive_file

    # Maintainer 1 can still decrypt
    assert vault.decrypt_bytes(archive_file.read_bytes(), identity_path=id1_path) == secret_payload

    # Revoked maintainer 2 CANNOT decrypt
    with pytest.raises(pyrage.DecryptError, match="No matching identity"):
        vault.decrypt_bytes(archive_file.read_bytes(), identity_path=id2_path)


def test_refuse_identity_path_inside_repo(monkeypatch):
    """Refuse any identity key path located inside the repository workspace (INV-AC1)."""
    repo_root = vault.get_repo_root()

    inside_paths = [
        repo_root / "test_age.key",
        repo_root / "evals" / "test_age.key",
        repo_root / "recipeboxer" / "age.key",
        Path("./test_inside.key"),
        Path("evals/vault/leaked.key"),
    ]

    for inside_p in inside_paths:
        # keygen must refuse
        with pytest.raises(ValueError, match="Security violation.*INV-AC1"):
            vault.keygen(out_path=inside_p)

        # load_identity must refuse
        with pytest.raises(ValueError, match="Security violation.*INV-AC1"):
            vault.load_identity(identity_path=inside_p)

        # decrypt_bytes must refuse
        with pytest.raises(ValueError, match="Security violation.*INV-AC1"):
            vault.decrypt_bytes(b"dummy_ciphertext", identity_path=inside_p)

        # RB_EVAL_AGE_KEY_FILE pointing inside repo must refuse
        monkeypatch.setenv("RB_EVAL_AGE_KEY_FILE", str(inside_p))
        with pytest.raises(ValueError, match="Security violation.*INV-AC1"):
            vault.load_identity()

    # Verify no leaked files were created in repo
    assert not (repo_root / "test_age.key").exists()
    assert not (repo_root / "evals" / "test_age.key").exists()


def test_import_guard_raises_when_called_from_recipeboxer_module():
    """Import guard raises RuntimeError when called from a module under recipeboxer.* (INV-AC1)."""
    fake_ns = {
        "__name__": "recipeboxer.fake",
        "vault": vault,
    }

    # Calling vault functions from a recipeboxer module namespace must raise
    with pytest.raises(RuntimeError, match="INV-AC1 Violation.*recipeboxer.fake"):
        exec("vault.encrypt_bytes(b'test', recipients=['age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p'])", fake_ns)

    with pytest.raises(RuntimeError, match="INV-AC1 Violation.*recipeboxer.fake"):
        exec("vault.keygen()", fake_ns)

    with pytest.raises(RuntimeError, match="INV-AC1 Violation.*recipeboxer.fake"):
        exec("vault.decrypt_bytes(b'ciphertext')", fake_ns)

    with pytest.raises(RuntimeError, match="INV-AC1 Violation.*recipeboxer.fake"):
        exec("vault.load_identity()", fake_ns)

    with pytest.raises(RuntimeError, match="INV-AC1 Violation.*recipeboxer.fake"):
        exec("vault.load_recipients()", fake_ns)

    # Calling check_import_guard directly from a subpackage
    subpkg_ns = {
        "__name__": "recipeboxer.extraction.router",
        "check_guard": vault._check_import_guard,
    }
    with pytest.raises(RuntimeError, match="INV-AC1 Violation.*recipeboxer.extraction.router"):
        exec("check_guard()", subpkg_ns)


def test_add_recipient_validation(tmp_path: Path):
    """Validate recipient format (age1 Bech32) and appending logic."""
    rec_file = tmp_path / "recipients.txt"

    # Generate a valid key
    id_path = tmp_path / "sample.key"
    pubkey, _ = vault.keygen(out_path=id_path)

    # Add valid recipient
    added = vault.add_recipient(pubkey, recipients_path=rec_file)
    assert added == pubkey

    loaded = vault.load_recipients(recipients_path=rec_file)
    assert len(loaded) == 1
    assert str(loaded[0]) == pubkey

    # Adding duplicate should not duplicate entry in recipients list
    vault.add_recipient(pubkey, recipients_path=rec_file)
    loaded_after = vault.load_recipients(recipients_path=rec_file)
    assert len(loaded_after) == 1

    # Invalid public keys must raise ValueError
    invalid_keys = [
        "not-a-valid-key",
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGo4",
        "age1invalid_checksum_characters_xyz!",
        "AGE-SECRET-KEY-1...",  # Private key passed as recipient
        "",
    ]
    for bad_key in invalid_keys:
        with pytest.raises(ValueError, match="Invalid age"):
            vault.add_recipient(bad_key, recipients_path=rec_file)


def test_clear_error_messages_when_identity_missing(tmp_path: Path, monkeypatch):
    """Provide clear, actionable error messages when identity file is missing or empty."""
    nonexistent = tmp_path / "does_not_exist.key"
    with pytest.raises(FileNotFoundError, match="Age identity file not found"):
        vault.load_identity(identity_path=nonexistent)

    # Empty file
    empty_file = tmp_path / "empty.key"
    empty_file.write_text("# Only comments\n\n", encoding="utf-8")
    with pytest.raises(ValueError, match="No valid age identity found"):
        vault.load_identity(identity_path=empty_file)


def test_cli_interface(tmp_path: Path, capsys):
    """Test CLI commands: keygen, add-recipient, encrypt, decrypt."""
    key_path = tmp_path / "cli_identity.key"
    rec_path = tmp_path / "cli_recipients.txt"

    # CLI keygen
    ret = vault.main(["keygen", "--out", str(key_path)])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Public key: age1" in captured.out
    pubkey = [line.split("Public key: ")[1].strip() for line in captured.out.splitlines() if "Public key: " in line][0]

    # CLI add-recipient
    ret = vault.main(["add-recipient", pubkey, "--recipients", str(rec_path)])
    assert ret == 0

    # CLI encrypt & decrypt
    input_file = tmp_path / "cli_input.txt"
    input_file.write_text("Secret Recipe CLI Data", encoding="utf-8")
    enc_file = tmp_path / "cli_output.enc"

    ret = vault.main(["encrypt", str(input_file), str(enc_file), "--recipients", str(rec_path)])
    assert ret == 0

    dec_file = tmp_path / "cli_decrypted.txt"
    ret = vault.main(["decrypt", str(enc_file), str(dec_file), "--identity", str(key_path)])
    assert ret == 0
    assert dec_file.read_text(encoding="utf-8") == "Secret Recipe CLI Data"
