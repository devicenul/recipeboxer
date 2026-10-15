import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from recipeboxer.correlation.linkmodel import card_descriptor
from recipeboxer.models.domain import VisualFingerprint
PCD=Path.home()/".cache/projects/recipeboxer-ba754a91"
KEYS=["has_title","has_ing","has_dir","no_recipe_sections","back","cut_end","cut_start","fwd_cue","cont_header","role_start","role_cont","role_standalone","role_unknown"]
def descs(spec):
    tag,deck=spec.split("/")
    p=PCD/"up4/dumps"/tag/f"{deck}.json"
    if not p.exists(): p=PCD/"up3/dumps"/tag/f"{deck}.json"
    d=json.loads(p.read_text())
    return [card_descriptor(VisualFingerprint.model_validate(f)) for f in d["fps"]]
fit=sum((descs(s) for s in sys.argv[1].split(",")),[])
freq={k:sum(x[k] for x in fit)/len(fit) for k in KEYS}
print("fit n",len(fit),{k:round(v,3) for k,v in freq.items()})
for s in sys.argv[2].split(","):
    ds=descs(s)
    atyp=[ [k for k in KEYS if (freq[k] if x[k]>=0.5 else 1-freq[k])<0.02] for x in ds]
    print(s, "frac_atypical=%.2f"%(sum(1 for a in atyp if a)/len(ds)), {k:sum(1 for a in atyp if k in a) for k in KEYS if any(k in a for a in atyp)})
import math
from collections import Counter
pat=lambda x: tuple(int(x[k]>=0.5) for k in KEYS)
fitc=Counter(pat(x) for x in fit)
def nll(x):
    return -sum(math.log((freq[k]*len(fit)+0.5)/(len(fit)+1)) if x[k]>=0.5 else math.log(((1-freq[k])*len(fit)+0.5)/(len(fit)+1)) for k in KEYS)
print("---")
for s in sys.argv[1].split(",")+sys.argv[2].split(","):
    ds=descs(s)
    unseen=sum(1 for x in ds if fitc[pat(x)]<2)/len(ds)
    print(s,"mean_nll=%.2f unseen_pattern=%.2f"%(sum(nll(x) for x in ds)/len(ds),unseen), "fwd=%.2f"%(sum(x['fwd_cue'] for x in ds)/len(ds)))
