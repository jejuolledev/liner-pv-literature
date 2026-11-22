"""어려운 시험지 후보 모으기(11/19). 제목만으로 가리기 어려운 것: 동물 증례, 약이 치료로 쓰인 증례,
다른 약이 의심인 증례, 제목에 case 가 없는 증례. 결과는 eval/pool_hard.json(사람이 읽고 gold_hard 에 고른다)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pvlit.liner import LinerClient
from pvlit.search import paper_key, clean_date
Q = [
 ("metformin", "metformin toxicosis in a dog"),
 ("levetiracetam", "levetiracetam adverse effect in a dog or cat"),
 ("atorvastatin", "atorvastatin myopathy rat model"),
 ("semaglutide", "semaglutide successful treatment case report"),
 ("vancomycin", "oral vancomycin successful treatment case report"),
 ("amiodarone", "amiodarone used to treat arrhythmia caused by another drug case report"),
 ("metformin", "patient on metformin developed adverse reaction to another drug case report"),
 ("atorvastatin", "atorvastatin colchicine interaction rhabdomyolysis patient"),
 ("semaglutide", "semaglutide aspiration during anesthesia patient"),
 ("pembrolizumab", "pembrolizumab rechallenge patient adverse event"),
]
c = LinerClient(); out = {}
for d, q in Q:
    for r in c.scholar(q, max_results=8):
        k = paper_key(r)
        if k in out: continue
        out[k] = {"key": k, "drug": d, "query": q, "title": r.get("title") or "", "abstract": r.get("description") or "",
                  "url": r.get("url") or "", "date": clean_date(r.get("date")), "journal": r.get("journal") or ""}
json.dump(list(out.values()), open(os.path.join(os.path.dirname(__file__), "pool_hard.json"), "w"), ensure_ascii=False, indent=1)
print("pool_hard", len(out), c.ledger.as_dict())
