"""어려운 시험지(11/19). 제목만 보고는 가리기 어려운 논문 28편. 정답은 Claude 가 초록을 읽고 정했고(사람 확인 아님),
근거 조각이 초록·제목에 글자 그대로 있는지 코드로 확인한 뒤에만 gold_hard.jsonl 에 쓴다.

판정 기준(GVP Module VI Rev 2, VI.B.1.1.2 를 따름):
- case=true: 새로 보고된 개별 환자(사람) + 해당 성분 투여 + 저자가 그 성분과 적어도 가능한 인과로 연결한 이상사례.
  자체 환자 증례 시리즈(여러 명)도 true. 제목에 case 가 없어도 같다. 저자가 의심 약으로 보면 병용·상호작용도 true.
- case=false: 이미 발표된 증례를 모은 문헌고찰·체계적 고찰(새 환자 없음 — 원 논문에서 이미 잡힌다),
  동물·시험관 연구, 다른 약만 의심인 증례(VI.B.1.1.2: 저자가 인과를 둔 약만 검토 대상), 빈도를 묻는 글.
- 애매해서 정답을 못 정한 것은 넣지 않았다(풀 hard 22 근육병·58 리팍시민·174 피하주입 호환성·34 효과 부족).

split: dev 는 프롬프트·검색어를 고칠 때 봐도 되는 것, holdout 은 고친 뒤 한 번만 재는 확인용(고치는 동안 열어 보지 않음).
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pvlit.verify import is_verbatim
H = os.path.dirname(os.path.abspath(__file__))
SRC = {"pool": json.load(open(os.path.join(H, "pool.json"))), "hard": json.load(open(os.path.join(H, "pool_hard.json")))}
# (출처, 번호, 정답, 종류, 근거1, 근거2)
ROWS = [
 # 증례인데 제목에 case 가 없거나 헷갈리는 것
 ("pool", 108, True, "제목에 case 없음·나이 없음", "The patient had been receiving pembrolizumab therapy", "Linear IgA Bullous Dermatosis"),
 ("pool", 167, True, "제목에 case 없음", "A 68-year-old male with persistent atrial fibrillation", "suggestive of Type 2 amiodarone-induced thyrotoxicosis"),
 ("pool", 170, True, "제목에 case 없음", "a 59-year-old female", "occurrence of TDPs leading to multiple electric cardioversions"),
 ("pool", 171, True, "제목에 case 없음", "a 29-year-old African American male", "This case report describes a patient who developed rhabdomyolysis"),
 ("pool", 92, True, "제목에 case 없음·나이 없음", "The patient’s lipid panel revealed grade 4 hypertriglyceridemia", "pembrolizumab-associated asymptomatic hypertriglyceridemia after nine doses of pembrolizumab"),
 ("pool", 148, True, "제목에 case 없음", "an 81‐year‐old man", "Amiodarone‐Induced Pulmonary Toxicity"),
 ("pool", 149, True, "제목에 case 없음", "a 68-year-old man", "developed acute hypoxic respiratory failure 2 weeks after initiation of amiodarone"),
 ("pool", 151, True, "베트남어·상품명(Cordaron)", "BN nữ 72 tuổi", "CƯỜNG GIÁP DO AMIODARON"),
 ("pool", 194, True, "자체 환자 15명 증례 시리즈", "To present 15 patients who experienced unusual adverse effects linked to levetiracetam", "Neuropsychiatric adverse effects predominated"),
 ("hard", 43, True, "제목이 비유(Savior and Foe)", "We present the case of a patient", "subsequently led to type II amiodarone-induced thyrotoxicosis"),
 ("hard", 23, True, "제목은 「흉내」, 본문은 스타틴 유발", "a 62-year-old Hispanic woman", "shortly after initiating atorvastatin therapy"),
 ("hard", 64, True, "마취 중 위 내용물(위험 상황)", "a 52-year-old obese man with type 2 diabetes mellitus receiving semaglutide", "which was considered a \"full stomach\" with a high risk of aspiration"),
 ("hard", 67, True, "제목에 성분명 없음(GLP-1 RA)", "the case of a 14-year-old female patient", "a distended antrum containing fluid and particulate matter consistent with a full stomach"),
 ("hard", 68, True, "두 명(자매)", "two biologically related female siblings, aged 44 and 41 years", "new-onset acneiform eruptions"),
 ("pool", 146, True, "초록 글자가 깨짐", "63-year-old female with a known case of chronic atrial fibrillation", "Amiodarone Pulmonary Toxicity"),
 ("pool", 199, True, "증례+문헌고찰", "A 17-year-old female", "acute toxic optic neuritis of the left eye associated with isotretinoin therapy"),
 # 증례처럼 보이지만 아닌 것
 ("pool", 29, False, "증례 보고들을 모은 체계적 고찰", "This systematic review aimed to summarize reported cases", ""),
 ("pool", 88, False, "제목에 case report, 실제는 발표된 42편 분석", "Forty-two case reports were carefully reviewed", ""),
 ("pool", 216, False, "증례 문헌 서술 고찰", "A narrative literature review was conducted", ""),
 ("pool", 190, False, "드문 부작용 고찰", "This review aims to increase awareness", ""),
 ("pool", 172, False, "빈도 질문(증례 언급만)", "particularly evident in clinical trials and case reports", ""),
 ("pool", 51, False, "발표된 증례 고찰 계획서", "This systematic review will summarize published case reports", ""),
 ("hard", 14, False, "동물(개)·다른 약(페노바르비톤) 의심", "Suspected phenobarbitone induced pemphigus vulgaris in a dog", ""),
 ("hard", 18, False, "동물(쥐)·예방 효과", "Foram utilizados 36 ratos wistar", ""),
 ("hard", 60, False, "다른 약(로수바스타틴) 의심, 아토르바스타틴은 바꾸기 전 약", "who was switched from 40 mg atorvastatin to 40 mg of rosuvastatin daily", ""),
 ("pool", 20, False, "시험관 상호작용 자료", "in vitro enzyme kinetic and inhibition kinetic measurements", ""),
 ("pool", 160, False, "동물·시험관 위주 체계적 고찰", "English-language in vitro, animal, and human studies", ""),
 ("pool", 104, False, "체계적 고찰", "This systematic review examined the incidence", ""),
]
# 확인용(holdout): 정답·종류를 섞어 절반. 번호를 정해 두고 바꾸지 않는다.
HOLDOUT = {("pool", 170), ("pool", 92), ("pool", 149), ("pool", 194), ("hard", 23), ("hard", 67), ("pool", 146), ("pool", 199),
           ("pool", 88), ("pool", 190), ("pool", 51), ("hard", 18), ("pool", 20), ("pool", 104)}
out = []
for src, i, case, kind, e1, e2 in ROWS:
    x = SRC[src][i]
    for q in (e1, e2):
        if q:
            assert is_verbatim(q, x["abstract"]) or is_verbatim(q, x["title"]), (src, i, q)
    out.append({"id": f"{'H' if src == 'hard' else 'P'}{i:03d}", "src": src, "drug": x["drug"], "case": case, "kind": kind,
                "split": "holdout" if (src, i) in HOLDOUT else "dev", "title": x["title"], "url": x["url"],
                "evidence_1": e1, "evidence_2": e2, "abstract": x["abstract"]})
with open(os.path.join(H, "gold_hard.jsonl"), "w") as f:
    for r in out: f.write(json.dumps(r, ensure_ascii=False) + "\n")
from collections import Counter
print(len(out), Counter((r["split"], r["case"]) for r in out))
