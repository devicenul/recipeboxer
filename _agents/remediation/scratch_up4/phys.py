import json,sys
from pathlib import Path
PCD=Path.home()/".cache/projects/recipeboxer-ba754a91"
names=["paper_lab","ink_hue","aspect_ratio","ruling","mirrored_wear","show_through"]
for spec in sys.argv[1:]:
    tag,deck=spec.split("/")
    p=PCD/"up4/dumps"/tag/f"{deck}.json"
    if not p.exists(): p=PCD/"up3/dumps"/tag/f"{deck}.json"
    d=json.loads(p.read_text())
    gold=d["gold"]; true_l={(g[i],g[i+1]) for g in gold for i in range(len(g)-1)}
    rows={True:[],False:[]}
    for e in d["evidence"]:
        f={x["name"]:x for x in e["features"]}
        v=[f.get(n,{}).get("log_lr") for n in names]
        raw=[f.get(n,{}).get("raw_value") for n in names[:3]]
        rows[(e["a"],e["b"]) in true_l].append((v,raw))
    def summ(L):
        out=[]
        for i,n in enumerate(names):
            xs=[r[0][i] for r in L if r[0][i] is not None]
            out.append(f"{n}:min={min(xs):.2f} mean={sum(xs)/len(xs):.2f}" if xs else f"{n}:-")
        return out
    print(deck,"TRUE",len(rows[True]),summ(rows[True]))
    print(deck,"FALSE",len(rows[False]),summ(rows[False]))
    print("  true raw:",[r[1] for r in rows[True]][:8])
