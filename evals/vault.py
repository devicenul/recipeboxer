"""RecipeBoxer Ground-Truth Vault Management.

Enforces multi-recipient age encryption via pyrage for evaluation ground truth.
All ground truth for benchmark tiers T1-T4 is stored strictly encrypted in
evals/vault/<tier>.gt.enc and may only be decrypted by authorized scorers
possessing an age identity configured outside the repository.

Architectural Invariant INV-AC1:
- Production code under recipeboxer/ may NEVER import or call evals.vault.
- Stack-frame inspection runtime guard enforces this constraint on import and call.
- Static AST tests in tests/anticheat/test_static_invariants.py enforce this statically.
- Age identities must reside strictly outside the repository workspace.
"""

from __future__ import annotations

import argparse
import inspect
import os
import sys
from pathlib import Path
from typing import Optional, Union

import pyrage
from pyrage import x25519


def _check_import_guard() -> None:
    """Enforce INV-AC1: No module under recipeboxer may import or call evals.vault.

    Enforced dynamically by inspect.stack() frame inspection and statically
    by tests/anticheat/test_static_invariants.py.
    """
    for frame_info in inspect.stack():
        module_name = frame_info.frame.f_globals.get("__name__", "")
        if module_name == "recipeboxer" or module_name.startswith("recipeboxer."):
            raise RuntimeError(
                f"INV-AC1 Violation: module '{module_name}' under recipeboxer "
                f"attempted to import or call evals.vault. Production code must never "
                f"access ground truth or vault tools."
            )


# Execute import guard at module load time
_check_import_guard()


def get_repo_root() -> Path:
    """Return the absolute Path to the repository root."""
    # evals/vault.py -> evals -> repo_root
    return Path(__file__).resolve().parent.parent


DEFAULT_KEY_PATH = Path("~/.config/recipeboxer/age.key")
DEFAULT_RECIPIENTS_PATH = get_repo_root() / "evals" / "vault" / "recipients.txt"
ENV_AGE_KEY_FILE = "RB_EVAL_AGE_KEY_FILE"


def _ensure_path_outside_repo(
    path: Union[str, Path], description: str = "Identity/key path"
) -> Path:
    """Ensure that an identity or key path does not reside inside the repository.

    Raises ValueError (INV-AC1 security violation) if the path resolves inside the repo.
    """
    resolved_path = Path(path).expanduser().resolve()
    repo_root = get_repo_root().resolve()

    inside = False
    try:
        resolved_path.relative_to(repo_root)
        inside = True
    except ValueError:
        pass

    if not inside:
        try:
            Path(path).expanduser().absolute().relative_to(get_repo_root().absolute())
            inside = True
        except ValueError:
            pass

    if inside or resolved_path == repo_root:
        raise ValueError(
            f"Security violation (INV-AC1): {description} '{path}' ({resolved_path}) "
            f"cannot be located inside the repository ({repo_root}). "
            f"Private identities and keys must remain strictly outside the workspace."
        )
    return resolved_path


def keygen(out_path: Optional[Union[str, Path]] = None) -> tuple[str, Path]:
    """Generate a new age identity keypair and write the identity to disk.

    Parameters
    ----------
    out_path:
        Destination path for the identity key file.
        Defaults to ~/.config/recipeboxer/age.key.
        Must reside outside the repository.

    Returns
    -------
    tuple[str, Path]
        A tuple of (public_key_string, resolved_identity_path).
    """
    _check_import_guard()
    target = DEFAULT_KEY_PATH if out_path is None else Path(out_path)
    resolved_target = _ensure_path_outside_repo(target, "Identity output path")

    identity = x25519.Identity.generate()
    pubkey = str(identity.to_public())
    ident_str = str(identity)

    # Ensure parent directory exists with mode 700
    os.makedirs(resolved_target.parent, mode=0o700, exist_ok=True)

    # Write file atomically with mode 600
    fd = os.open(
        resolved_target,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("# created by evals.vault keygen\n")
        f.write(f"# public key: {pubkey}\n")
        f.write(f"{ident_str}\n")
    os.chmod(resolved_target, 0o600)

    return pubkey, resolved_target


def load_identity(
    identity_path: Optional[Union[str, Path]] = None,
) -> x25519.Identity:
    """Load an age identity from the specified path or environment.

    Path resolution order:
    1. Explicit identity_path argument
    2. RB_EVAL_AGE_KEY_FILE environment variable
    3. Default ~/.config/recipeboxer/age.key

    Raises
    ------
    ValueError:
        If the resolved path is inside the repo (INV-AC1 violation) or content is invalid.
    FileNotFoundError:
        If the identity file does not exist.
    """
    _check_import_guard()
    if identity_path is not None:
        raw_path = Path(identity_path)
    else:
        env_val = os.environ.get(ENV_AGE_KEY_FILE)
        if env_val:
            raw_path = Path(env_val)
        else:
            raw_path = DEFAULT_KEY_PATH

    resolved = _ensure_path_outside_repo(raw_path, "Identity path")

    if not resolved.exists():
        raise FileNotFoundError(
            f"Age identity file not found at '{resolved}'. "
            f"Set {ENV_AGE_KEY_FILE} or generate an identity with 'python -m evals.vault keygen'."
        )

    content = resolved.read_text(encoding="utf-8")
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            return x25519.Identity.from_str(line)
        except Exception as e:
            raise ValueError(
                f"Could not parse age identity from line in '{resolved}': {e}"
            ) from e

    raise ValueError(
        f"No valid age identity found in '{resolved}'. Expected Bech32 string starting with AGE-SECRET-KEY-1..."
    )


def load_recipients(
    recipients_path: Optional[Union[str, Path]] = None,
) -> list[x25519.Recipient]:
    """Load authorized recipient public keys from recipients.txt.

    Lines starting with '#' and blank lines are ignored.
    """
    _check_import_guard()
    target = DEFAULT_RECIPIENTS_PATH if recipients_path is None else Path(recipients_path)
    resolved = target.expanduser().resolve()

    if not resolved.exists():
        raise FileNotFoundError(f"Vault recipients file not found at '{resolved}'.")

    content = resolved.read_text(encoding="utf-8")
    recipients: list[x25519.Recipient] = []
    for idx, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip trailing comments if any
        clean = line.split("#")[0].strip()
        if not clean:
            continue
        try:
            recipients.append(x25519.Recipient.from_str(clean))
        except Exception as e:
            raise ValueError(
                f"Invalid age recipient public key at line {idx} in '{resolved}': '{clean}'. "
                f"Expected Bech32 string starting with age1...: {e}"
            ) from e

    if not recipients:
        raise ValueError(
            f"No valid recipients found in '{resolved}'. "
            f"Add at least one recipient with 'python -m evals.vault add-recipient <pubkey>'."
        )

    return recipients


def add_recipient(
    pubkey: str, recipients_path: Optional[Union[str, Path]] = None
) -> str:
    """Validate and append an age recipient public key to recipients.txt."""
    _check_import_guard()
    clean_pubkey = pubkey.strip()
    try:
        x25519.Recipient.from_str(clean_pubkey)
    except Exception as e:
        raise ValueError(
            f"Invalid age public key format '{clean_pubkey}': {e}. "
            f"Expected Bech32 public key starting with 'age1...'"
        ) from e

    target = DEFAULT_RECIPIENTS_PATH if recipients_path is None else Path(recipients_path)
    resolved = target.expanduser().resolve()

    os.makedirs(resolved.parent, exist_ok=True)

    if resolved.exists():
        content = resolved.read_text(encoding="utf-8")
        existing_lines = [l.split("#")[0].strip() for l in content.splitlines()]
        if clean_pubkey in existing_lines:
            return clean_pubkey  # Already registered

        # Ensure preceding newline if not empty and doesn't end with newline
        prefix = ""
        if content and not content.endswith("\n"):
            prefix = "\n"
        with open(resolved, "a", encoding="utf-8") as f:
            f.write(f"{prefix}{clean_pubkey}\n")
    else:
        with open(resolved, "w", encoding="utf-8") as f:
            f.write("# RecipeBoxer Vault Recipients\n")
            f.write("# Public age keys of authorized evaluators and CI\n")
            f.write(f"{clean_pubkey}\n")

    return clean_pubkey


def encrypt_bytes(
    data: Union[bytes, str],
    recipients: Optional[list[Union[str, x25519.Recipient]]] = None,
    recipients_path: Optional[Union[str, Path]] = None,
    armored: bool = False,
) -> bytes:
    """Encrypt plaintext bytes in-memory to all configured recipients.

    Parameters
    ----------
    data:
        Plaintext payload as bytes or string.
    recipients:
        Optional explicit list of Recipient objects or pubkey strings.
        If omitted, loaded from recipients_path.
    recipients_path:
        Optional path to recipients.txt.
    armored:
        Whether to produce ASCII armored age output. Defaults to False (binary).
    """
    _check_import_guard()
    if recipients is None:
        recipient_objs = load_recipients(recipients_path)
    else:
        recipient_objs = []
        for r in recipients:
            if isinstance(r, str):
                recipient_objs.append(x25519.Recipient.from_str(r.strip()))
            else:
                recipient_objs.append(r)

    if not recipient_objs:
        raise ValueError("Cannot encrypt: no recipients provided or configured.")

    payload = data.encode("utf-8") if isinstance(data, str) else data
    return pyrage.encrypt(payload, recipient_objs, armored=armored)


def decrypt_bytes(
    ciphertext: bytes,
    identity: Optional[Union[str, x25519.Identity]] = None,
    identity_path: Optional[Union[str, Path]] = None,
) -> bytes:
    """Decrypt age ciphertext in-memory using the authorized identity.

    Parameters
    ----------
    ciphertext:
        Encrypted age payload bytes.
    identity:
        Optional explicit Identity object or private key string.
        If omitted, loaded from identity_path or default environment.
    identity_path:
        Optional path to identity key file.
    """
    _check_import_guard()
    if identity is None:
        ident_obj = load_identity(identity_path)
    elif isinstance(identity, str):
        ident_obj = x25519.Identity.from_str(identity.strip())
    else:
        ident_obj = identity

    try:
        return pyrage.decrypt(ciphertext, [ident_obj])
    except pyrage.DecryptError as e:
        raise pyrage.DecryptError(
            f"Decryption failed: No matching identity found for this ciphertext. "
            f"Ensure your age identity is registered in recipients.txt."
        ) from e


def encrypt(
    in_path: Union[str, Path],
    out_path: Union[str, Path],
    recipients: Optional[list[Union[str, x25519.Recipient]]] = None,
    recipients_path: Optional[Union[str, Path]] = None,
    armored: bool = False,
) -> Path:
    """Encrypt a file on disk to all recipients."""
    _check_import_guard()
    source = Path(in_path).expanduser().resolve()
    dest = Path(out_path).expanduser().resolve()

    if not source.exists():
        raise FileNotFoundError(f"Input file for encryption not found: '{source}'")

    plaintext = source.read_bytes()
    ciphertext = encrypt_bytes(
        plaintext, recipients=recipients, recipients_path=recipients_path, armored=armored
    )

    os.makedirs(dest.parent, exist_ok=True)
    dest.write_bytes(ciphertext)
    return dest


def decrypt(
    in_enc_path: Union[str, Path],
    out_path: Optional[Union[str, Path]] = None,
    identity: Optional[Union[str, x25519.Identity]] = None,
    identity_path: Optional[Union[str, Path]] = None,
) -> bytes:
    """Decrypt an age-encrypted file.

    If out_path is specified and not '-', writes decrypted bytes to out_path.
    Returns the decrypted bytes.
    """
    _check_import_guard()
    source = Path(in_enc_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Encrypted input file not found: '{source}'")

    ciphertext = source.read_bytes()
    decrypted = decrypt_bytes(ciphertext, identity=identity, identity_path=identity_path)

    if out_path is not None and str(out_path) != "-":
        dest = Path(out_path).expanduser().resolve()
        os.makedirs(dest.parent, exist_ok=True)
        dest.write_bytes(decrypted)

    return decrypted


def rekey(
    target_dir: Union[str, Path],
    recipients_path: Optional[Union[str, Path]] = None,
    identity: Optional[Union[str, x25519.Identity]] = None,
    identity_path: Optional[Union[str, Path]] = None,
) -> list[Path]:
    """Re-encrypt all *.enc files in target_dir (and its archive/ subdir) to the current recipients.

    Decrypts each file entirely in-memory and re-encrypts to the current
    recipients list without ever writing plaintext to disk.
    """
    _check_import_guard()
    folder = Path(target_dir).expanduser().resolve()
    if not folder.is_dir():
        raise NotADirectoryError(f"Target directory for rekeying not found: '{folder}'")

    recipients = load_recipients(recipients_path)
    ident_obj = load_identity(identity_path) if identity is None else identity

    enc_files = sorted(folder.glob("*.enc"))
    # WP-T3A: archived opt-in tiers (e.g. evals/vault/archive/t3a.gt.enc) are rekeyed too.
    archive_dir = folder / "archive"
    if archive_dir.is_dir():
        enc_files += sorted(archive_dir.glob("*.enc"))
    rekeyed: list[Path] = []

    for file_path in enc_files:
        ciphertext = file_path.read_bytes()
        plaintext = decrypt_bytes(ciphertext, identity=ident_obj)
        new_ciphertext = encrypt_bytes(plaintext, recipients=recipients)
        file_path.write_bytes(new_ciphertext)
        rekeyed.append(file_path)

    return rekeyed


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint for evals.vault."""
    _check_import_guard()
    parser = argparse.ArgumentParser(
        prog="evals.vault",
        description="RecipeBoxer ground-truth vault management CLI (multi-recipient age encryption)",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # keygen
    p_keygen = subparsers.add_parser("keygen", help="Generate an age identity key")
    p_keygen.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Destination path for identity file (default: ~/.config/recipeboxer/age.key)",
    )

    # encrypt
    p_enc = subparsers.add_parser("encrypt", help="Encrypt file to current recipients")
    p_enc.add_argument("in_file", type=Path, help="Input plaintext file")
    p_enc.add_argument("out_file", type=Path, help="Output encrypted file (.enc)")
    p_enc.add_argument(
        "--recipients",
        type=Path,
        default=None,
        help="Path to recipients.txt (default: evals/vault/recipients.txt)",
    )

    # decrypt
    p_dec = subparsers.add_parser("decrypt", help="Decrypt an age-encrypted file")
    p_dec.add_argument("in_file", type=Path, help="Input encrypted file (.enc)")
    p_dec.add_argument(
        "out_file",
        nargs="?",
        default="-",
        help="Output decrypted file or '-' for stdout (default: -)",
    )
    p_dec.add_argument(
        "--identity",
        type=Path,
        default=None,
        help=f"Path to age identity file (default: ${ENV_AGE_KEY_FILE} or ~/.config/recipeboxer/age.key)",
    )

    # rekey
    p_rekey = subparsers.add_parser("rekey", help="Re-encrypt all *.enc in a directory")
    p_rekey.add_argument("dir", type=Path, help="Directory containing .enc files")
    p_rekey.add_argument(
        "--recipients",
        type=Path,
        default=None,
        help="Path to recipients.txt (default: evals/vault/recipients.txt)",
    )
    p_rekey.add_argument(
        "--identity",
        type=Path,
        default=None,
        help=f"Path to age identity file (default: ${ENV_AGE_KEY_FILE} or ~/.config/recipeboxer/age.key)",
    )

    # add-recipient
    p_add = subparsers.add_parser(
        "add-recipient", help="Validate and append an age recipient public key"
    )
    p_add.add_argument("pubkey", type=str, help="Age public key (age1...)")
    p_add.add_argument(
        "--recipients",
        type=Path,
        default=None,
        help="Path to recipients.txt (default: evals/vault/recipients.txt)",
    )

    args = parser.parse_args(argv)

    try:
        if args.subcommand == "keygen":
            pubkey, target = keygen(out_path=args.out)
            print(f"Public key: {pubkey}")
            print(f"Identity key written to: {target} (mode 600)")
            return 0

        elif args.subcommand == "encrypt":
            out_path = encrypt(
                in_path=args.in_file,
                out_path=args.out_file,
                recipients_path=args.recipients,
            )
            print(f"Encrypted {args.in_file} -> {out_path}")
            return 0

        elif args.subcommand == "decrypt":
            data = decrypt(
                in_enc_path=args.in_file,
                out_path=None if args.out_file == "-" else args.out_file,
                identity_path=args.identity,
            )
            if args.out_file == "-":
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
            else:
                print(f"Decrypted {args.in_file} -> {args.out_file}")
            return 0

        elif args.subcommand == "rekey":
            rekeyed = rekey(
                target_dir=args.dir,
                recipients_path=args.recipients,
                identity_path=args.identity,
            )
            print(f"Rekeyed {len(rekeyed)} file(s) in {args.dir} to current recipients.")
            return 0

        elif args.subcommand == "add-recipient":
            added = add_recipient(args.pubkey, recipients_path=args.recipients)
            print(f"Added recipient: {added}")
            return 0

    except Exception as e:
        sys.stderr.write(f"evals.vault error: {e}\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
