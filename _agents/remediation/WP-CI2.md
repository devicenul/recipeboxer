# WP-CI2 — Make GitHub Actions CI green

Base: `remediation/phase0` @ 6537994. Failing run: 38836533784 / job 116766822177 (17 failed, 3 errors).
No git state was changed. `setup.sh` was **not** touched or staged (`M setup.sh` in the working copy is the user's private change).

## Root causes and fixes

| # | Failure | Root cause | Fix |
|---|---|---|---|
| a | 6 zip-slip tests (`..\\..\\evil.txt`, `C:/evil.txt`, `C:\\evil.txt`, `D:/…`, `assets/scans/..\\..\\…`) | **Real security gap.** `_safe_extract` used `os.path.normpath`, which follows host-OS rules: on Linux a backslash is an ordinary character and `C:` is a normal name, so these paths were treated as safe. The containment check also used `str.startswith` on paths, so a sibling directory with the same prefix (`out` vs `out2`) counted as inside. | New `_portable_relative_member_path()` in `recipeboxer/storage/archive.py` gives the same result on every OS: `\` counts as a separator; drive-letter (`C:x`, `C:/x`, `C:\x`), UNC (`\\srv\…`, `//srv/…`), NUL and `:`-in-segment paths are rejected; `..` is resolved and rejected if it climbs above the root; a leading `/` or `\` is stripped (this keeps the existing "contained" test contract). Containment is now checked with `Path.parents`. Manifest `assets[].archive_path` values go through the same validator inside `validate_archive_manifest`. On import, `asset.filename` is cut down to a portable basename before it is used as a destination path (it was another traversal vector). Existing tests are unchanged. |
| b | 7 `test_browse_directory` tests | The tests patched `askdirectory`, but `tk.Tk()` still needs an X display. Locally, tkinter isn't installed at all. | Added an autouse fixture that puts a fake `tkinter`/`tkinter.filedialog` in `sys.modules`, so the tests need neither a display nor tkinter. In production (`routes.py`), a missing tkinter or a failing `Tk()` (no `$DISPLAY`) now returns `success=false, error="GUI subsystem unavailable: <detail>"`. Two tests added for these cases. |
| c | `test_setup_sh_fails_when_no_compatible_python` | **Test isolation bug, not setup.sh.** The test set `PATH=<fake>:/bin:/usr/bin`. `/usr/bin` contains a real `python3.12` (on CI) or `python3.13` (in the VM), and setup.sh's candidate list (`python3.14…python3.10`) found it. The test then ran a real setup. | PATH now contains only the fake bin dir (fake `python3`/`python` plus a `dirname` symlink). `bash` is called by absolute path, `VIRTUAL_ENV`/`PYTHONHOME`/`PYTHONPATH` are dropped, timeout is 60 s. I also ran the **committed** `git show HEAD:setup.sh` the same way: it exits 1 with "Python 3.10 or higher is required". **No setup.sh patch is needed.** |
| d | `test_wp_r2_curation.py`: 3 errors + 1 failure | Hard-coded `/home/droid/.config/recipeboxer/age.key`. | New `identity_path` fixture: uses `$RB_EVAL_AGE_KEY_FILE` if set, otherwise `~/.config/recipeboxer/age.key`. If the file is missing, the test skips with a "Maintainer-only…" reason. The offline tests (`DECKS.json`, `ATTRIBUTION.md`) still run. |
| e | `test_glyph_coverage`: Caveat cmap missing / DID NOT RAISE | Fonts **are committed** (`cardforge/fonts/*.ttf`, OFL, byte-identical in git). CI had no **fontTools**: it isn't a declared dependency, so `get_font_cmap()` returned None. The raster fallback only flagged glyphs that render blank, but Caveat's `.notdef` is a visible box, so unmapped characters passed. | Added `fonttools>=4.40.0` to `cardforge/pyproject.toml`; CI's `pip install -e ./cardforge` now installs it (checked with a pip dry-run in a fresh venv: fonttools 4.67.0 resolves). The raster fallback now also treats a glyph as missing if it renders identically to `.notdef` (probe U+10FFFD). New `cardforge/tests/test_wp_ci2_glyph_fallback.py` covers the fallback with `TTFont=None`. No skips needed. |
| f | Workflow hygiene | — | `eval-run.yml`: `push: [main]` trigger and its `if:` branch removed; only `workflow_dispatch` and the `run-eval` label remain, both behind `eval-live`. `ci.yml`: `paths-ignore` for `**/*.md`, `docs/**`, `_agents/**` on push and pull_request. All jobs `runs-on: ubuntu-24.04`. `actions/checkout` v4.2.2→**v7.0.1**, `setup-python` v5.4.0→**v7.0.0**, `setup-node` v4.2.0→**v7.1.0** (all `using: node24`, confirmed in each `action.yml`). Docs: `docs/CI.md` (new "Runner & action pinning" section, updated trigger table), `docs/ROADMAP.md` (P2 test-hygiene row and §3.1.4 marked done). |

## Contract deviations / decisions
- **Action pinning:** you asked to pin by tag. I pinned the tag's **full commit SHA with the tag as a comment** (e.g. `@3d3c42e… # v7.0.1`). That is the repo's existing convention and GitHub's hardening guidance. SHAs were resolved with `gh api repos/actions/<a>/commits/<tag>`. If you want bare `@v7`, it's a one-line change per action.
- Not bumped, but noted in `docs/CI.md`: `upload-artifact` v4.6.1, `download-artifact` v4.1.9, `github-script` v7.0.1. The newer majors have breaking changes (download v8 fails on digest mismatch by default; github-script v9 drops `require('@actions/github')`), and the eval-score path can't be tested offline. The frontend job still uses `node-version: "20"`, which is EOL since April 2026. Bumping to 22/24 is recommended but wasn't verified here.
- I ran the full suite once, as you asked. WAVE3 §0 normally says targeted tests only.

## Commands and results
- Archive and import tests, plus new tests: 105 passed.
- Touched files plus `tests/anticheat`: **202 passed, 6 skipped** (276 s).
- CI emulation for (d): `RB_EVAL_AGE_KEY_FILE=/nonexistent/age.key pytest tests/test_wp_r2_curation.py -rs` → 2 passed, 4 skipped (Maintainer-only reason). With the real key: 6 passed.
- CI emulation for (e): fallback tests run with fontTools disabled (`TTFont=None`) and pass. Fonts are tracked in git, so no fetch step is needed.
- Full suite (`timeout 1800 … pytest tests cardforge/tests -q -p no:randomly --continue-on-collection-errors -rs`): **2535 passed, 10 skipped, 0 failed** (533 s). Log: `$PCD/full_ci2.log`. The skips are live-key, pwsh, dist-artifact and one vault-key (`test_wp_scale_robustness`) environment skips.

## Files changed
`recipeboxer/storage/archive.py`, `recipeboxer/api/routes.py`, `cardforge/generator.py`, `cardforge/pyproject.toml`,
`tests/test_browse_directory.py`, `tests/test_scripts.py`, `tests/test_wp_r2_curation.py`,
new: `tests/test_wp_ci2_archive_paths.py`, `cardforge/tests/test_wp_ci2_glyph_fallback.py`,
`.github/workflows/{ci,eval-run,eval-score}.yml`, `docs/CI.md`, `docs/ROADMAP.md`, this note.
`recipeboxer/api/static/index.html` is unchanged.

## Risks / notes for the orchestrator
- Stricter archive validation now rejects archives whose member names contain `:` or drive/UNC prefixes. Archives made by RecipeBoxer itself only use `manifest.json` and `assets/scans/<sha>.<ext>`, so this should not affect real users.
- `TestSetupShPOSIX::test_setup_sh_execution_idempotence` and `test_setup_sh_invocation_from_different_cwd` run the real setup.sh, which creates/updates the repo's `.venv` (gitignored) using the **working-copy** setup.sh. This is pre-existing behaviour and left unchanged.
- `paths-ignore`: if CI ever becomes a required check, docs-only PRs would wait forever for it (explained in docs/CI.md).
- `.github/workflows` changes need a token with `workflow` scope when pushing.
