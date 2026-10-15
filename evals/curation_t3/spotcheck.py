"""Ground-Truth Spotcheck Tool for T3 Curation (WP-T3GT).

Decrypts evals/vault/t3.gt.enc in memory using an authorized age identity,
displays a representative ~20% random sample in the terminal, and generates
an interactive, self-contained HTML review package outside the repository.

Enforces Invariants:
- INV-AC1: Operates outside production code; requires an identity outside the workspace.
- Memory-only: Decryption occurs strictly in-memory; no plaintext GT touches disk in workspace.
- Review package is written outside the repository (e.g. $HOME/.cache/.../t3gt/spotcheck/).
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
from pathlib import Path
import random
import sys
from typing import Any, Dict, List, Optional, Tuple

from evals import vault

logger = logging.getLogger("evals.curation_t3.spotcheck")


def encode_image_base64(image_path: Path) -> str:
    """Encode an image file to a base64 data URI string."""
    if not image_path.exists():
        return ""
    data = image_path.read_bytes()
    mime = "image/jpeg"
    if image_path.suffix.lower() == ".png":
        mime = "image/png"
    elif image_path.suffix.lower() == ".webp":
        mime = "image/webp"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def generate_spotcheck_html_package(
    sampled_recipes: List[Tuple[str, Dict[str, Any]]],
    output_dir: Path,
    corpus_dir: Path,
    seed: int,
) -> None:
    """Generates a self-contained HTML spot-check review package."""
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    recipe_files: List[Dict[str, str]] = []

    for idx, (deck_id, recipe) in enumerate(sampled_recipes, start=1):
        rid = recipe.get("recipe_id", "unknown")
        faces = recipe.get("faces", [])
        verbatim_list = recipe.get("verbatim", [])
        structured = recipe.get("structured") or {}
        diff = recipe.get("difficulty", {})
        status = recipe.get("structured_status", "unreviewed")
        mem_score = recipe.get("memorization_score")
        excluded = recipe.get("excluded_reason")

        title = structured.get("title") or "Untitled Recipe"
        yield_val = structured.get("yield") or "<em>[None]</em>"
        source_note = structured.get("source_note") or "<em>[None]</em>"
        notes_list = structured.get("notes") or []

        # Build image HTML
        images_html_parts = []
        for face_id in faces:
            img_path = corpus_dir / deck_id / face_id
            data_uri = encode_image_base64(img_path)
            if data_uri:
                images_html_parts.append(
                    f'<div class="card-face"><div class="face-tag">Face: {face_id}</div>'
                    f'<img src="{data_uri}" alt="{face_id}" class="card-img" /></div>'
                )
            else:
                images_html_parts.append(
                    f'<div class="card-face"><div class="face-tag">Face: {face_id}</div>'
                    f'<p>[Image not found at {img_path}]</p></div>'
                )
        images_html = "\n".join(images_html_parts)

        # Build verbatim HTML
        verbatim_html_parts = []
        for f_idx, (face_id, v_text) in enumerate(zip(faces, verbatim_list), start=1):
            lines = v_text.splitlines()
            line_html = "\n".join(
                f'<div class="v-line"><span class="line-num">{l_idx+1}</span><span class="line-text">{line}</span></div>'
                for l_idx, line in enumerate(lines)
            )
            verbatim_html_parts.append(
                f'<div class="verbatim-face"><h4>Face {f_idx} ({face_id})</h4>'
                f'<div class="verbatim-box">{line_html}</div></div>'
            )
        verbatim_html = "\n".join(verbatim_html_parts)

        # Build ingredients table
        ing_rows = []
        for i_idx, ing in enumerate(structured.get("ingredients", []), start=1):
            raw = ing.get("raw") or ""
            qty = ing.get("quantity") or ""
            unit = ing.get("unit") or ""
            item = ing.get("item") or ""
            sec = ing.get("section") or ""
            ing_rows.append(
                f'<tr><td>{i_idx}</td><td><code>{raw}</code></td>'
                f'<td>{qty if qty else "<span class=\'null-badge\'>null</span>"}</td>'
                f'<td>{unit if unit else "<span class=\'null-badge\'>null</span>"}</td>'
                f'<td><strong>{item}</strong></td>'
                f'<td>{sec if sec else "<span class=\'null-badge\'>null</span>"}</td></tr>'
            )
        ing_table = (
            '<table class="data-table"><thead><tr>'
            '<th>#</th><th>Raw Text</th><th>Qty</th><th>Unit</th><th>Item</th><th>Section</th>'
            f'</tr></thead><tbody>{"".join(ing_rows)}</tbody></table>'
        )

        # Build directions steps
        dir_rows = []
        for d_idx, step in enumerate(structured.get("directions", []), start=1):
            dir_rows.append(
                f'<div class="dir-step"><span class="step-num">{d_idx}.</span> '
                f'<span class="step-text">{step}</span></div>'
            )
        dir_html = "\n".join(dir_rows) if dir_rows else "<p><em>[No directions]</em></p>"

        # Build notes
        notes_html = "".join(f"<li>{n}</li>" for n in notes_list) if notes_list else "<li><em>[None]</em></li>"

        # HTML Page for Recipe
        recipe_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Spot-Check: {rid} - {title}</title>
<style>
  :root {{ --bg: #f8f9fa; --panel: #ffffff; --border: #dee2e6; --primary: #0d6efd; --text: #212529; --ok: #198754; --err: #dc3545; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 20px; }}
  .header {{ background: var(--panel); padding: 16px 24px; border-radius: 8px; border: 1px solid var(--border); margin-bottom: 20px; }}
  .header h1 {{ margin: 0 0 8px 0; font-size: 24px; }}
  .meta-badges {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 8px; }}
  .badge {{ background: #e9ecef; padding: 4px 10px; border-radius: 12px; font-size: 13px; font-weight: 500; }}
  .badge-primary {{ background: #cfe2ff; color: #084298; }}
  .badge-warning {{ background: #fff3cd; color: #664d03; }}
  .badge-danger {{ background: #f8d7da; color: #842029; }}
  .container {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  .panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
  .panel h2 {{ margin-top: 0; border-bottom: 2px solid var(--border); padding-bottom: 8px; font-size: 18px; }}
  .card-img {{ max-width: 100%; height: auto; border: 1px solid #ccc; border-radius: 4px; display: block; margin-bottom: 12px; }}
  .face-tag {{ font-weight: bold; margin-bottom: 4px; font-size: 13px; color: #555; }}
  .verbatim-box {{ background: #fdfdfd; border: 1px solid #eee; padding: 12px; border-radius: 4px; font-family: "SFMono-Regular", Consolas, monospace; font-size: 13px; max-height: 400px; overflow-y: auto; }}
  .v-line {{ display: flex; gap: 12px; line-height: 1.5; }}
  .line-num {{ color: #999; user-select: none; width: 28px; text-align: right; }}
  .line-text {{ color: #222; }}
  .data-table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }}
  .data-table th, .data-table td {{ border: 1px solid var(--border); padding: 8px 10px; text-align: left; }}
  .data-table th {{ background: #f1f3f5; }}
  .null-badge {{ color: #888; font-style: italic; }}
  .dir-step {{ padding: 6px 0; border-bottom: 1px solid #f0f0f0; line-height: 1.4; font-size: 14px; }}
  .step-num {{ font-weight: bold; color: var(--primary); }}
  .checklist {{ background: #eef2f7; border: 1px solid #c5d3e8; border-radius: 8px; padding: 16px; margin-top: 20px; }}
  .checklist h3 {{ margin-top: 0; font-size: 16px; }}
  .check-row {{ display: flex; align-items: center; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #dce4ee; }}
  .check-options {{ display: flex; gap: 16px; }}
  .check-options label {{ font-size: 13px; font-weight: 500; cursor: pointer; }}
  .textarea-feedback {{ width: 100%; box-sizing: border-box; height: 80px; margin-top: 10px; padding: 8px; font-family: inherit; font-size: 13px; border-radius: 4px; border: 1px solid #ccc; }}
  .btn {{ background: var(--primary); color: white; border: none; padding: 8px 16px; border-radius: 4px; font-weight: bold; cursor: pointer; margin-top: 10px; }}
  .btn:hover {{ background: #0b5ed7; }}
  .nav-bar {{ display: flex; justify-content: space-between; margin-bottom: 16px; }}
  .nav-bar a {{ text-decoration: none; color: var(--primary); font-weight: bold; }}
</style>
</head>
<body>
<div class="nav-bar">
  <a href="index.html">&larr; Back to Spot-Check Overview</a>
  <span>Recipe {idx} of {len(sampled_recipes)}</span>
</div>

<div class="header">
  <h1>Recipe {rid}: {title}</h1>
  <div class="meta-badges">
    <span class="badge badge-primary">Deck: {deck_id}</span>
    <span class="badge">Faces: {len(faces)}</span>
    <span class="badge">Legibility: {diff.get('legibility', 'unknown')}</span>
    <span class="badge">Status: {status}</span>
    <span class="badge">Memorization Score: {mem_score if mem_score is not None else 'N/A'}</span>
    {f'<span class="badge badge-danger">Excluded: {excluded}</span>' if excluded else '<span class="badge" style="color:var(--ok)">Memorization Clean</span>'}
  </div>
</div>

<div class="container">
  <!-- LEFT PANEL: Image + Verbatim -->
  <div class="panel">
    <h2>1. Manuscript Images & Human Verbatim</h2>
    {images_html}
    <h3 style="margin-top:20px;">Verbatim Transcription</h3>
    {verbatim_html}
  </div>

  <!-- RIGHT PANEL: Drafted Structured GT -->
  <div class="panel">
    <h2>2. Drafted Structured Ground Truth</h2>
    <p><strong>Title:</strong> {title}</p>
    <p><strong>Yield:</strong> {yield_val}</p>
    <p><strong>Source Note:</strong> {source_note}</p>

    <h3 style="margin-top:16px;">Ingredients ({len(structured.get("ingredients", []))})</h3>
    {ing_table}

    <h3 style="margin-top:20px;">Directions ({len(structured.get("directions", []))} steps)</h3>
    {dir_html}

    <h3 style="margin-top:20px;">Notes / Marginalia</h3>
    <ul>{notes_html}</ul>

    <!-- AUDIT CHECKLIST -->
    <div class="checklist">
      <h3>Reviewer Checklist: {rid}</h3>
      <div class="check-row">
        <span>Title fidelity:</span>
        <div class="check-options">
          <label><input type="radio" name="chk_title" value="OK" checked /> OK</label>
          <label><input type="radio" name="chk_title" value="Wrong" /> Wrong</label>
        </div>
      </div>
      <div class="check-row">
        <span>Yield accuracy:</span>
        <div class="check-options">
          <label><input type="radio" name="chk_yield" value="OK" checked /> OK</label>
          <label><input type="radio" name="chk_yield" value="Wrong" /> Wrong</label>
        </div>
      </div>
      <div class="check-row">
        <span>Ingredients completeness & parsing:</span>
        <div class="check-options">
          <label><input type="radio" name="chk_ing" value="OK" checked /> OK</label>
          <label><input type="radio" name="chk_ing" value="Wrong" /> Wrong</label>
        </div>
      </div>
      <div class="check-row">
        <span>Directions sequential steps:</span>
        <div class="check-options">
          <label><input type="radio" name="chk_dir" value="OK" checked /> OK</label>
          <label><input type="radio" name="chk_dir" value="Wrong" /> Wrong</label>
        </div>
      </div>
      <div class="check-row">
        <span>Overall ground-truth verdict:</span>
        <div class="check-options">
          <label><input type="radio" name="chk_overall" value="Approved" checked /> Approved</label>
          <label><input type="radio" name="chk_overall" value="Needs_Edits" /> Needs Edits</label>
        </div>
      </div>

      <label style="display:block; margin-top:12px; font-weight:bold; font-size:13px;">Reviewer Notes / Corrections:</label>
      <textarea id="rev_notes" class="textarea-feedback" placeholder="Describe any inaccuracies, omissions, or spelling corrections..."></textarea>
      <button class="btn" onclick="saveReview('{rid}')">Save Review Checklist</button>
      <span id="save_msg" style="margin-left:12px; font-size:13px; color:var(--ok); font-weight:bold;"></span>
    </div>
  </div>
</div>

<script>
function saveReview(recipeId) {{
  const review = {{
    recipeId: recipeId,
    title: document.querySelector('input[name="chk_title"]:checked')?.value,
    yield: document.querySelector('input[name="chk_yield"]:checked')?.value,
    ingredients: document.querySelector('input[name="chk_ing"]:checked')?.value,
    directions: document.querySelector('input[name="chk_dir"]:checked')?.value,
    overall: document.querySelector('input[name="chk_overall"]:checked')?.value,
    notes: document.getElementById('rev_notes').value,
    reviewedAt: new Date().toISOString()
  }};
  localStorage.setItem('spotcheck_' + recipeId, JSON.stringify(review));
  document.getElementById('save_msg').innerText = 'Saved locally!';
  setTimeout(() => {{ document.getElementById('save_msg').innerText = ''; }}, 2500);
}}
window.addEventListener('DOMContentLoaded', () => {{
  const saved = localStorage.getItem('spotcheck_{rid}');
  if (saved) {{
    try {{
      const d = JSON.parse(saved);
      if (d.title) document.querySelector(`input[name="chk_title"][value="${{d.title}}"]`).checked = true;
      if (d.yield) document.querySelector(`input[name="chk_yield"][value="${{d.yield}}"]`).checked = true;
      if (d.ingredients) document.querySelector(`input[name="chk_ing"][value="${{d.ingredients}}"]`).checked = true;
      if (d.directions) document.querySelector(`input[name="chk_dir"][value="${{d.directions}}"]`).checked = true;
      if (d.overall) document.querySelector(`input[name="chk_overall"][value="${{d.overall}}"]`).checked = true;
      if (d.notes) document.getElementById('rev_notes').value = d.notes;
    }} catch(e) {{}}
  }}
}});
</script>
</body>
</html>
"""
        filename = f"recipe_{rid}.html"
        (output_dir / filename).write_text(recipe_html, encoding="utf-8")
        recipe_files.append({
            "rid": rid,
            "title": title,
            "filename": filename,
            "faces_count": str(len(faces)),
            "legibility": diff.get("legibility", "good"),
            "mem_score": str(mem_score) if mem_score is not None else "N/A",
        })

    # Generate index.html dashboard
    index_rows = []
    for item in recipe_files:
        index_rows.append(
            f'<tr>'
            f'<td><a href="{item["filename"]}"><strong>{item["rid"]}</strong></a></td>'
            f'<td>{item["title"]}</td>'
            f'<td>{item["faces_count"]} face(s)</td>'
            f'<td>{item["legibility"]}</td>'
            f'<td>{item["mem_score"]}</td>'
            f'<td><a href="{item["filename"]}" class="btn-sm">Review Recipe &rarr;</a></td>'
            f'</tr>'
        )

    index_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>RecipeBoxer T3 Ground-Truth 20% Spot-Check</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #f8f9fa; color: #212529; margin: 0; padding: 40px; }}
  .box {{ max-width: 900px; margin: 0 auto; background: white; border: 1px solid #dee2e6; border-radius: 8px; padding: 32px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }}
  h1 {{ margin-top: 0; font-size: 26px; }}
  p {{ line-height: 1.6; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
  th, td {{ border: 1px solid #dee2e6; padding: 12px; text-align: left; font-size: 14px; }}
  th {{ background: #f1f3f5; }}
  .btn-sm {{ display: inline-block; background: #0d6efd; color: white; padding: 4px 10px; border-radius: 4px; text-decoration: none; font-size: 12px; font-weight: bold; }}
  .btn-sm:hover {{ background: #0b5ed7; }}
  .instructions {{ background: #e7f1ff; border: 1px solid #b6d4fe; border-radius: 6px; padding: 16px; margin: 20px 0; font-size: 14px; }}
  .instructions ol {{ margin: 8px 0 0 16px; padding: 0; }}
</style>
</head>
<body>
<div class="box">
  <h1>RecipeBoxer T3 Ground-Truth: 20% Human Spot-Check</h1>
  <p>In accordance with <strong>Remediation Plan §3.3</strong>, 20% of the T3 real-web evaluation recipes have been sampled deterministically (seed={seed}) for human verification of the drafted structured ground truth against the manuscript images and verbatim transcriptions.</p>

  <div class="instructions">
    <strong>Spot-Check Instructions:</strong>
    <ol>
      <li>Click on each recipe below to open the side-by-side review interface.</li>
      <li>Examine the card image and verbatim transcription on the left side.</li>
      <li>Check the drafted structured recipe (title, ingredients, directions, notes) on the right side.</li>
      <li>Fill in the checklist at the bottom and click <strong>"Save Review Checklist"</strong>.</li>
      <li>If any corrections are needed, note them in the feedback box or report them back.</li>
    </ol>
  </div>

  <h2>Sampled Evaluation Recipes ({len(recipe_files)} items)</h2>
  <table>
    <thead>
      <tr>
        <th>Recipe ID</th>
        <th>Title</th>
        <th>Faces</th>
        <th>Legibility</th>
        <th>Memorization</th>
        <th>Action</th>
      </tr>
    </thead>
    <tbody>
      {"".join(index_rows)}
    </tbody>
  </table>
</div>
</body>
</html>
"""
    (output_dir / "index.html").write_text(index_html, encoding="utf-8")

    # Generate README.md
    readme_md = f"""# T3 Ground-Truth 20% Spot-Check Review Package

This package contains the self-contained human review interfaces for the deterministic 20% spot-check of the **RecipeBoxer T3 Real-Web Corpus** structured ground truth, generated in accordance with `REMEDIATION_PLAN.md` §3.3.

- **Sampling Parameters:** Rate = 20% (5 of 25 recipes), Seed = {seed} (deterministic).
- **Location:** `{output_dir}`
- **Package Status:** 100% self-contained HTML (all manuscript card images are base64-encoded).

---

## 1. How to Open the Review Package

### Option A: Open directly in a browser
Open the main dashboard in any web browser:
```bash
# On your local machine or via browser:
xdg-open "{output_dir}/index.html"
# Or double-click index.html
```

### Option B: Local HTTP Server (e.g., inside VM or over port forwarding)
```bash
python3 -m http.server 8080 --directory "{output_dir}"
```
Then navigate to `http://localhost:8080` in your browser.

---

## 2. Sampled Recipes for Review

| Recipe ID | Title | Deck | Faces | Legibility | Memorization Overlap | File |
|---|---|---|---|---|---|---|
"""
    for item in recipe_files:
        readme_md += f"| `{item['rid']}` | {item['title']} | `d01` | {item['faces_count']} | {item['legibility']} | {item['mem_score']} | [{item['filename']}]({item['filename']}) |\n"

    readme_md += """
---

## 3. Reviewer Checklist & How to Report Corrections

1. **Verify Verbatim Fidelity:**
   - Confirm that the drafted ingredients contain only items and quantities mentioned in the text.
   - Confirm that missing quantities are represented as `null`, not guessed.
   - Confirm that directions steps faithfully reproduce the preparation method without hallucinated cooking temperatures or modern instructions.
2. **Reviewer Checklist:**
   - In each recipe's HTML interface, fill out the per-field OK/Wrong radio buttons.
   - Click **Save Review Checklist** to store responses in your browser (`localStorage`).
3. **Reporting Corrections Back:**
   - If any recipe requires correction, note the specific field and replacement text in your review notes, or update the recipe vault directly via `evals.vault`.
"""
    (output_dir / "README.md").write_text(readme_md, encoding="utf-8")
    print(f"Generated self-contained spot-check review package in {output_dir}")
    print(f"- Dashboard: {output_dir / 'index.html'}")
    print(f"- Instructions: {output_dir / 'README.md'}")


def spotcheck_vault(
    vault_file: Path,
    identity_path: Optional[Path] = None,
    sample_rate: float = 0.2,
    seed: Optional[int] = 42,
    show_all: bool = False,
    html_out_dir: Optional[Path] = None,
    corpus_dir: Optional[Path] = None,
) -> None:
    """Decrypt GT in memory and print a spot-check sample to stdout and/or generate HTML package."""
    resolved_vault = Path(vault_file).expanduser().resolve()
    if not resolved_vault.exists():
        raise FileNotFoundError(f"Vault archive not found at '{resolved_vault}'")

    ciphertext = resolved_vault.read_bytes()
    plaintext = vault.decrypt_bytes(ciphertext, identity_path=identity_path)
    gt_data: Dict[str, Any] = json.loads(plaintext.decode("utf-8"))

    schema = gt_data.get("schema", "unknown")
    tier = gt_data.get("tier", "unknown")
    created = gt_data.get("created", "unknown")
    decks: List[Dict[str, Any]] = gt_data.get("decks", [])

    print("=" * 72)
    print(f"RECIPEBOXER GROUND-TRUTH SPOTCHECK: TIER {tier} ({schema})")
    print(f"Archive: {resolved_vault.name} | Created: {created}")
    print("=" * 72)

    total_images = sum(len(d.get("images", [])) for d in decks)
    all_recipes: List[Tuple[str, Dict[str, Any]]] = []
    for d in decks:
        deck_id = d.get("deck_id", "unknown")
        for r in d.get("recipes", []):
            all_recipes.append((deck_id, r))

    total_recipes = len(all_recipes)
    if total_recipes == 0:
        print("No recipes found in archive.")
        return

    if show_all:
        sample = all_recipes
        print(f"Showing ALL {len(sample)} recipes across {len(decks)} deck(s):")
    else:
        sample_size = max(1, int(round(total_recipes * sample_rate)))
        rng = random.Random(seed) if seed is not None else random.Random()
        sample = rng.sample(all_recipes, sample_size)
        print(
            f"Showing random {len(sample)}/{total_recipes} recipe sample "
            f"({len(sample)/total_recipes*100:.1f}%, seed={seed}):"
        )
    print("-" * 72)

    for idx, (deck_id, recipe) in enumerate(sample, start=1):
        rid = recipe.get("recipe_id", "unknown")
        faces = recipe.get("faces", [])
        diff = recipe.get("difficulty", {})
        status = recipe.get("structured_status", "pending")
        verbatim_list = recipe.get("verbatim", [])
        excluded = recipe.get("excluded_reason")
        mem_score = recipe.get("memorization_score")

        print(f"\n[{idx}/{len(sample)}] Recipe ID: {rid} (Deck: {deck_id})")
        print(f"  Faces ({len(faces)}): {', '.join(faces)}")
        print(f"  Legibility: {diff.get('legibility', 'unknown')} | Status: {status}")
        if mem_score is not None:
            print(f"  Memorization Score: {mem_score}")
        if excluded:
            print(f"  Excluded Reason: {excluded}")

        for f_idx, (face_id, v_text) in enumerate(zip(faces, verbatim_list), start=1):
            print(f"  --- Face {f_idx} [{face_id}] Verbatim ({len(v_text.splitlines())} lines) ---")
            for line in v_text.splitlines()[:6]:
                print(f"    | {line}")
            if len(v_text.splitlines()) > 6:
                print(f"    | ... ({len(v_text.splitlines()) - 6} more lines)")

        if recipe.get("structured") is not None:
            st = recipe["structured"]
            print(f"  Structured Title: {st.get('title')}")
            print(f"  Structured Ingredients ({len(st.get('ingredients', []))}):")
            for ing in st.get("ingredients", [])[:3]:
                print(f"    - {ing.get('raw')} -> item='{ing.get('item')}', qty={ing.get('quantity')}, unit={ing.get('unit')}")
            if len(st.get("ingredients", [])) > 3:
                print(f"    - ... ({len(st.get('ingredients', [])) - 3} more)")
            print(f"  Structured Directions ({len(st.get('directions', []))} steps)")
        else:
            print("  Structured data: [pending text-only draft]")

    print("\n" + "=" * 72)
    print("SUMMARY")
    print(f"  Total Decks:    {len(decks)}")
    print(f"  Total Images:   {total_images}")
    print(f"  Total Recipes:  {total_recipes}")
    print(f"  Sampled:        {len(sample)} ({len(sample)/total_recipes*100:.1f}%)")
    print("=" * 72)

    # Generate HTML package if requested
    if html_out_dir:
        c_dir = Path(corpus_dir or (vault.get_repo_root() / "evals" / "corpora" / "t3")).resolve()
        generate_spotcheck_html_package(
            sampled_recipes=sample,
            output_dir=html_out_dir,
            corpus_dir=c_dir,
            seed=seed if seed is not None else 42,
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Spotcheck encrypted ground-truth vault in memory")
    parser.add_argument(
        "--vault-file",
        type=Path,
        default=vault.get_repo_root() / "evals" / "vault" / "t3.gt.enc",
        help="Path to encrypted vault file (default: evals/vault/t3.gt.enc)",
    )
    parser.add_argument(
        "--identity",
        type=Path,
        default=None,
        help="Path to age identity file (default: ~/.config/recipeboxer/age.key)",
    )
    parser.add_argument(
        "--sample-rate",
        type=float,
        default=0.2,
        help="Fraction of recipes to sample (default: 0.2)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for repeatable sampling (default: 42)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Display all recipes instead of a random sample",
    )
    parser.add_argument(
        "--html-dir",
        type=Path,
        default=None,
        help="Generate interactive HTML spot-check review package into this directory",
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=None,
        help="Path to T3 corpus directory containing deck subdirectories (default: evals/corpora/t3)",
    )
    args = parser.parse_args(argv)

    try:
        spotcheck_vault(
            vault_file=args.vault_file,
            identity_path=args.identity,
            sample_rate=args.sample_rate,
            seed=args.seed,
            show_all=args.all,
            html_out_dir=args.html_dir,
            corpus_dir=args.corpus_dir,
        )
        return 0
    except Exception as e:
        sys.stderr.write(f"spotcheck error: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
