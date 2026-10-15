import json,sys
from pathlib import Path
PCD=Path.home()/".cache/projects/recipeboxer-ba754a91"
for spec in sys.argv[1:]:
    tag,deck=spec.split("/")
    p=PCD/"up4/dumps"/tag/f"{deck}.json"
    if not p.exists(): p=PCD/"up3/dumps"/tag/f"{deck}.json"
    d=json.loads(p.read_text())
    gold=d["gold"]; true_l={(g[i],g[i+1]) for g in gold for i in range(len(g)-1)}
    pred={(c[i],c[i+1]) for c in d["chains"] for i in range(len(c)-1)}
    ev={(e["a"],e["b"]):e for e in d["evidence"]}
    sv={}
    for v in d["verifications"]:
        s=[v["anchor"]]+list(v["continuations"] or [])
        for i in range(len(s)-1):
            sv[(s[i],s[i+1])]=(i==0, len(v.get("verified_quotes") or []), len(v.get("unverified_quotes") or []), v.get("confidence"))
    cnt={}
    for e in pred|true_l:
        k=("pred" if e in pred else "miss", e in true_l, e in sv, (sv.get(e) or (0,0,0,0))[1]>0)
        cnt[k]=cnt.get(k,0)+1
    print(deck, sorted(cnt.items()))
    nonsv=[(round(ev[e]["p_link"],3), e in true_l) for e in pred if e not in sv]
    if nonsv: print("   pred w/o SV:", nonsv[:20])
