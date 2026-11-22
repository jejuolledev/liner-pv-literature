"""검색어 바꾸기 전에 잰다(11/19): 새 질의(증례 시리즈·상품명)가 지난 실제 한 주(검토 #1, 168편)에 없던 증례 후보를 몇 편 더 찾는가.
지난 회차 질의별 기여도는 DB 에서 센다. 새 질의 결과 중 #1 에 없던 논문만 판정한다. 결과: eval/results/query_compare_<시각>.json"""
import collections, datetime as dt, json, os, sqlite3, sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pvlit.judge import judge_paper, verdict
from pvlit.liner import LinerClient
from pvlit.search import DEFAULT_DRUGS, paper_key, mentions_drug
H = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(H)
db = sqlite3.connect(os.path.join(ROOT, "data", "pv.sqlite3"))
rows = db.execute("select drug, key, queries, verdict from papers where run_id=1").fetchall()
old = {(d, k) for d, k, _, _ in rows}
contrib = collections.defaultdict(lambda: collections.Counter())
for d, k, q, v in rows:
    t = sorted({x.replace(d, "{d}") for x in json.loads(q)})
    for x in t:
        contrib[x]["papers"] += 1; contrib[x]["candidates"] += v == "candidate"
    if len(t) == 1:
        contrib[t[0]]["only_this_query"] += 1; contrib[t[0]]["only_this_query_candidates"] += v == "candidate"
NEW = ["{d} case series adverse reaction"]
c = LinerClient(); b0 = c.balance_usd(); found = {}
for d, names in DEFAULT_DRUGS.items():
    qs = [t.format(d=d) for t in NEW] + [f"{b} case report" for b in names[1:3]]
    for q in qs:
        for r in c.scholar(q, max_results=20, date_range="past_year"):
            k = paper_key(r)
            if (d, k) in old: continue
            p = found.setdefault((d, k), {"drug": d, "key": k, "title": r.get("title") or "", "abstract": r.get("description") or "",
                                          "url": r.get("url") or "", "queries": [], "mentions_drug": mentions_drug(r, names)})
            p["queries"].append(q)
todo = [p for p in found.values() if p["mentions_drug"]]
with ThreadPoolExecutor(5) as ex: outs = list(ex.map(lambda p: judge_paper(c, p, p["drug"], DEFAULT_DRUGS[p["drug"]]), todo))
newc = [{"drug": p["drug"], "title": p["title"], "url": p["url"], "queries": p["queries"], "verdict": verdict(j)} for p, j in zip(todo, outs)]
by_q = collections.Counter(q.replace(p["drug"], "{d}") if p["drug"] in q else "{brand} case report"
                           for p in newc if p["verdict"] == "candidate" for q in set(p["queries"]))
res = {"when": dt.datetime.now().isoformat(timespec="seconds"), "old_run_contribution": {k: dict(v) for k, v in contrib.items()},
       "new_queries_unseen_papers": len(found), "unseen_with_drug_name": len(todo),
       "unseen_verdicts": dict(collections.Counter(p["verdict"] for p in newc)), "new_candidates_by_query": dict(by_q),
       "new_candidates": [p for p in newc if p["verdict"] == "candidate"], "ledger": c.ledger.as_dict(),
       "balance_before": b0, "balance_after": c.balance_usd()}
out = os.path.join(H, "results", "query_compare_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
json.dump(res, open(out, "w"), ensure_ascii=False, indent=1)
print(json.dumps({k: v for k, v in res.items() if k != "new_candidates"}, ensure_ascii=False, indent=1))
for p in res["new_candidates"]: print(" +", p["drug"], p["queries"], p["title"][:90])
