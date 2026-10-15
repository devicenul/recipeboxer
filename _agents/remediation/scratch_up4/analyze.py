"""WP-UP4 dump analysis: false links / missed links with their evidence, and deck descriptor stats."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from recipeboxer.correlation.linkmodel import card_descriptor  # noqa: E402
from recipeboxer.models.domain import VisualFingerprint  # noqa: E402

PCD = Path.home() / ".cache/projects/recipeboxer-ba754a91"
KEYS = ["has_title", "has_ing", "has_dir", "dir_only", "no_recipe_sections", "back", "cut_end", "cut_start",
        "fwd_cue", "cont_header", "role_start", "role_cont", "role_standalone", "role_unknown", "is_complete", "expects_cont"]


def load(tag, deck):
    roots = [PCD / "up4" / "dumps" / tag, PCD / "up3" / "dumps" / tag]
    for r in roots:
        if (r / f"{deck}.json").exists():
            return json.loads((r / f"{deck}.json").read_text())
    raise FileNotFoundError(tag + "/" + deck)


def stats(d):
    fps = [VisualFingerprint.model_validate(f) for f in d["fps"]]
    ds = [card_descriptor(f) for f in fps]
    return {k: round(sum(x[k] for x in ds) / len(ds), 2) for k in KEYS}, {f.card_id: f for f in fps}


def main(tag, decks):
    for deck in decks.split(","):
        d = load(tag, deck)
        st, fpm = stats(d)
        print("==", deck, d["summary"]["P"], d["summary"]["R"], st)
        gold = d["gold"]
        true_l = {(g[i], g[i + 1]) for g in gold for i in range(len(g) - 1)}
        pred = {(c[i], c[i + 1]) for c in d["chains"] for i in range(len(c) - 1)}
        ev = {(e["a"], e["b"]): e for e in d["evidence"]}
        svpos = set()
        for v in d["verifications"]:
            s = [v["anchor"]] + list(v["continuations"] or [])
            for i in range(len(s) - 1):
                svpos.add((s[i], s[i + 1]))
        for kind, edges in (("FALSE", pred - true_l), ("MISSED", true_l - pred)):
            for e in sorted(edges):
                p = ev.get(e, {}).get("p_link")
                fa, fb = fpm[e[0]], fpm[e[1]]
                print(f"  {kind} p={p} svpos={e in svpos} | A title={fa.visible_title!r} last={fa.last_lines[-1:] } cut={fa.text_cut_off_end}"
                      f" | B title={fb.visible_title!r} first={fb.first_lines[:1]} role={fb.role}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
