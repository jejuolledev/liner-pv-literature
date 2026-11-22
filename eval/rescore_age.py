"""저장된 측정 결과에 「환자 나이 꼴이면 확인 필요」 규칙(11/19 검토 반영)을 다시 적용한다(라이너 호출 없음).
그리고 어려운 시험지에서 정답이 갈릴 수 있는 3편(P108·P194·H067, 독립 검토 지적)을 뺀 숫자도 낸다."""
import glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pvlit.judge import age_cue
H = os.path.dirname(os.path.abspath(__file__))
AMBIG = {"P108", "P194", "H067"}
def m(rows):
    k = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}; s = dict(k)
    for case, v in rows:
        for d, pred in ((s, v == "candidate"), (k, v != "not_case")):
            d[("tp" if pred else "fn") if case else ("fp" if pred else "tn")] += 1
    return {"strict": s, "kept": k}
out = {}
hard = {json.loads(l)["id"]: json.loads(l) for l in open(os.path.join(H, "gold_hard.jsonl"))}
for label, pats in (("hard_before", ["hard_before_dev_*", "hard_before_holdout_*"]), ("hard_after", ["hard_after2_dev_*", "hard_after_holdout_*"])):
    rows = []
    for p in pats:
        d = json.load(open(sorted(glob.glob(os.path.join(H, "results", p)))[-1]))
        for x in d["details"]:
            g = hard[x["id"]]; v = x["verdict"]
            if label == "hard_after" and v == "not_case" and age_cue(g["title"], g["abstract"]): v = "check"
            rows.append((x["id"], g["split"], g["case"], v))
    out[label] = {"all28": m([(c, v) for _, _, c, v in rows]), "holdout": m([(c, v) for _, s, c, v in rows if s == "holdout"]),
                  "without_ambiguous_25": m([(c, v) for i, _, c, v in rows if i not in AMBIG]),
                  "check_ids": [i for i, _, _, v in rows if v == "check"]}
gold = {json.loads(l)["id"]: json.loads(l) for l in open(os.path.join(H, "gold.jsonl"))}
d = json.load(open(os.path.join(H, "results", "20261119_204820.json")))
rows = []
for x in d["details"]:
    g = gold[x["id"]]; v = x["verdict"]
    if v == "not_case" and age_cue(g["title"], g["abstract"]): v = "check"
    rows.append((g["case"], v))
out["gold60_after_age_rule"] = m(rows)
json.dump(out, open(os.path.join(H, "results", "rescore_age_20261119.json"), "w"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False))
