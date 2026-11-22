"""저장된 측정 결과(eval/results/*.json)의 모델 출력에 지금의 원문 대조 규칙을 다시 적용한다(라이너 호출 없음)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pvlit.verify import check_quotes
H = os.path.dirname(os.path.abspath(__file__))
gold = {json.loads(l)["id"]: json.loads(l) for l in open(os.path.join(H, "gold.jsonl"))}
NAMES = {"semaglutide": ["semaglutide", "Ozempic", "Wegovy", "Rybelsus"], "atorvastatin": ["atorvastatin", "Lipitor"],
         "metformin": ["metformin", "Glucophage"], "pembrolizumab": ["pembrolizumab", "Keytruda"]}
for f in sys.argv[1:]:
    d = json.load(open(f)); q = ok = all3 = 0; bad = []
    for x in d["details"]:
        g = gold[x["id"]]
        if not x["model"]: continue
        c = check_quotes(x["model"], "" if d.get("no_title") else g["title"], g["abstract"], NAMES.get(g["drug"], [g["drug"]]))
        for k, v in c.items():
            if v["status"] != "missing":
                q += 1; ok += v["status"] == "ok"
                if v["status"] != "ok": bad.append((x["id"], k, v["quote"][:60], v["why"]))
        if g["case"] and x["verdict"] == "candidate" and all(v["status"] == "ok" for v in c.values()): all3 += 1
    print(os.path.basename(f), "quotes", q, "ok", ok, "true cases all3", all3); [print("  ", b) for b in bad]

# 근거 위치 겹침: 증례에서 모델이 뽑은 환자·부작용 문장이 정답표 근거 문장(Claude 가 정함, 사람 확인 전)과 초록의 같은 자리를 가리키는가
from pvlit.verify import find_span
def overlap(a, b):
    return a and b and a[0] < b[1] and b[0] < a[1]
for f in sys.argv[1:]:
    d = json.load(open(f)); res = {"patient_quote": [0, 0], "event_quote": [0, 0]}; miss = []
    for x in d["details"]:
        g = gold[x["id"]]
        if not (g["case"] and x["model"]): continue
        for k, gk in (("patient_quote", "evidence_patient"), ("event_quote", "evidence_event")):
            ms = find_span(x["model"][k], g["abstract"]); gs = find_span(g[gk], g["abstract"])
            res[k][1] += 1
            if overlap(ms, gs): res[k][0] += 1
            else: miss.append((x["id"], k, x["model"][k][:50], "| gold:", g[gk][:50]))
    print(os.path.basename(f), "근거 위치 겹침", {k: f"{a}/{b}" for k, (a, b) in res.items()})
    for m in miss: print("  ", m)
