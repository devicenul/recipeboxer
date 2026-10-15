import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from recipeboxer.correlation.linkmodel import card_descriptor
from recipeboxer.correlation import guards as G
from recipeboxer.models.domain import VisualFingerprint
from recipeboxer.models.correlation import PairEvidence
PCD=Path.home()/".cache/projects/recipeboxer-ba754a91"
for spec in sys.argv[1:]:
    tag,deck=spec.split("/")
    p=PCD/"up4/dumps"/tag/f"{deck}.json"
    if not p.exists(): p=PCD/"up3/dumps"/tag/f"{deck}.json"
    d=json.loads(p.read_text())
    fps={f["card_id"]:VisualFingerprint.model_validate(f) for f in d["fps"]}
    ds={k:card_descriptor(v) for k,v in fps.items()}
    gold=d["gold"]; true_l={(g[i],g[i+1]) for g in gold for i in range(len(g)-1)}
    pred={(c[i],c[i+1]) for c in d["chains"] for i in range(len(c)-1)}
    ev={(e["a"],e["b"]):PairEvidence.model_validate(e) for e in d["evidence"]}
    q={}
    for v in d["verifications"]:
        s=[v["anchor"]]+list(v["continuations"] or [])
        qq=(len(v.get("verified_quotes") or []),len(v.get("unverified_quotes") or []),v.get("confidence") or 0)
        for i in range(len(s)-1): q[(s[i],s[i+1])]=qq
    out=[]
    for e in sorted(true_l|pred):
        a,b=e
        g=[]
        if G.is_nonrecipe(ds[a],fps[a]) or G.is_nonrecipe(ds[b],fps[b]): g.append("NONREC")
        if e in ev and G.physical_conflict(ev[e]): g.append("PHYS")
        if G.starts_new_recipe(fps[a],fps[b],ds[b]): g.append("TITLE"+("ok" if G.sv_positive_ok(q.get(e),G.STRONG_MIN_SV_CONF) else "X"))
        if e in q and not G.sv_positive_ok(q[e],G.STRICT_MIN_SV_CONF): g.append("SVweak%s"%(q[e],))
        if g: out.append(("T" if e in true_l else "F")+("p" if e in pred else "m")+":"+",".join(g))
    print(deck,len(true_l),out)
