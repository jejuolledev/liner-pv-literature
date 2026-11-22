"""정답표 후보 논문 모으기(라이너 학술 검색, 1년 범위). 결과는 eval/pool.json.
증례 질의와 증례 아닌 질의를 섞어 받는다. 정답은 Claude 가 초록을 읽고 GVP 기준으로 판정해 gold.jsonl 에 적었다(사람 확인 전)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pvlit.liner import LinerClient
from pvlit.search import paper_key, clean_date
DRUGS = ["atorvastatin", "semaglutide", "metformin", "pembrolizumab", "vancomycin", "amiodarone", "levetiracetam", "isotretinoin"]
Q = ["{d} case report", "{d} randomized controlled trial", "{d} systematic review", "{d} cohort study adverse events"]
c = LinerClient()
pool = {}
for d in DRUGS:
    for t in Q:
        q = t.format(d=d)
        for r in c.scholar(q, max_results=8):
            k = paper_key(r)
            if k in pool: continue
            pool[k] = {"key": k, "drug": d, "query": q, "title": r.get("title") or "", "abstract": r.get("description") or "",
                       "url": r.get("url") or "", "date": clean_date(r.get("date")), "journal": r.get("journal") or ""}
json.dump(list(pool.values()), open(os.path.join(os.path.dirname(__file__), "pool.json"), "w"), ensure_ascii=False, indent=1)
print("pool", len(pool), c.ledger.as_dict())
