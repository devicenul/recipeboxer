import asyncio, sys
sys.path.insert(0, ".")
sys.path.insert(0, "tests")
from test_wp_gd_engine import _make_fp, CardRole, SetVerification, ScriptedLLMClient, GroupingEngine, InMemoryImageProvider
from recipeboxer.correlation.linkmodel import default_models, LINK_FEATURES

fp1 = _make_fp("card_1", role=CardRole.SEQUENCE_START, title="Lemon Cake")
fp2 = _make_fp("card_2", role=CardRole.SEQUENCE_CONTINUATION)
sv = SetVerification(anchor="card_1", continuations=["card_2"], rejected=[], evidence_quotes=[], confidence=0.92,
                     model="m", prompt_id="set_verify_v1", prompt_version="v1")
eng = GroupingEngine()
res = asyncio.run(eng.group([fp1, fp2], InMemoryImageProvider({}), llm_client=ScriptedLLMClient([sv, sv])))
print(res.chains, res.stats)
for r in eng.last_rows.get("link", []):
    print(r[0], dict(zip(LINK_FEATURES, list(r[1]))))
m = default_models()["link"]
print(m.__dict__ if hasattr(m, "__dict__") else m)
