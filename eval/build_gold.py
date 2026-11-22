"""정답표 60편 만들기. 정답은 Claude 가 초록을 읽고 GVP 기준으로 판정했다(2026-11-19, 사람 확인 전). 근거는 초록 안 문장 조각이며,
아래 코드가 그 조각이 초록에 글자 그대로 있는지 확인한 뒤에만 gold.jsonl 에 쓴다.
case=true 기준: 개별 환자 + 해당 성분 투여 + 그 성분과 연결된 이상사례(셋 다)."""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pvlit.verify import is_verbatim
H = os.path.dirname(os.path.abspath(__file__))
pool = json.load(open(os.path.join(H, "pool.json")))
POS = {  # idx: (환자 근거, 이상사례 근거)
 0: ("A 45-year-old Ethiopian woman", "progressive, symmetric bilateral SNHL 3 weeks after starting atorvastatin"),
 1: ("An 89-year-old male patient", "a case of myopathy probably caused by concomitant use of atorvastatin and almonertinib"),
 2: ("A man in his 80s had been taking atorvastatin", "rhabdomyolysis due to a potential drug–drug interaction between atorvastatin and methylprednisolone"),
 3: ("An 81- year- old man", "developed bilateral lower limb pain suggestive of peripheral neuropathy during long-term atorvastatin therapy"),
 4: ("We present a 70-year-old male", "considering statin-induced neutropenia"),
 28: ("a 70-year-old woman presented to the emergency department", "drug-induced lupus, likely triggered by semaglutide"),
 30: ("a 50-year-old man", "experienced transient intestinal ischemia following his first Wegovy dosage"),
 31: ("a 58-year-old African American woman", "developed a marked exacerbation of her seizure-like episodes approximately 13 weeks after initiating semaglutide therapy"),
 32: ("a 26-year-old woman", "developed SPM shortly after the self-administered use of semaglutide"),
 34: ("a 50-year-old man with obesity receiving weekly semaglutide 2.4 mg", "he developed generalized burning dysesthesia"),
 58: ("a vulnerable patient with intellectual", "fatal\noutcome following intentional metformin intoxication"),
 60: ("A 62-year-old man", "metformin-induced liver injury"),
 61: ("a case of 65-year-old women", "Metformin-associated lactic acidosis"),
 62: ("a 51-year-old man", "ten days after initiating metformin"),
 63: ("in a patient with previously unrecognized obstructive uropathy", "severe AKI while on metformin therapy"),
 64: ("the 68-year-old male patient", "a notable presentation of metformin toxicity"),
 65: ("A 62-year-old male", "leading to a diagnosis of MALA"),
 86: ("a young male patient", "After 22 cycles of pembrolizumab treatment, the patient developed lethargy and marked metabolic acidosis"),
 87: ("an 83‐year‐old woman", "10 weeks after starting pembrolizumab"),
 90: ("a 52-year-old man", "after two years of pembrolizumab therapy, presented with bilateral ocular redness and photopsias"),
 91: ("A 76-year-old man", "TTP following a single dose of pembrolizumab"),
 113: ("a 2-year-old male child", "the patient developed a hypersensitivity reaction following intravenous administration of vancomycin"),
 114: ("a 64-year-old female patient", "vancomycin-induced DIIHA"),
 118: ("a 71-year-old female", "later diagnosed with VFS"),
 119: ("a 59-year-old woman", "severe left ventricular systolic dysfunction"),
 142: ("a 66-year-old man receiving long-term amiodarone therapy", "suspicion of amiodarone-induced pneumonitis"),
 143: ("a 56-year-old man with arterial hypertension", "developed painless, bilateral sequential visual loss three months after initiation of amiodarone therapy"),
 144: ("a 61-year-old female patient", "acute pancreatitis"),
 145: ("A 50-year-old man", "developed encephalopathy and laboratory evidence of ALF"),
 168: ("a 77-year-old female patient", "Levetiracetam was administered, which provoked hemolytic anemia"),
}
NEG_MANUAL = {
 5: "we developed the first C. elegans model of SRD5A3-CDG",
 117: "without new incidence reports of acute kidney injury",
 35: "Output reduceddramatically",
 195: "who achieved complete remission following treatment with oral isotretinoin",
 202: "achieved complete clearance with low-dose oral isotretinoin",
}
NEG_AUTO = [22,24,36,46,52,56,80,81,100,101,107,136,138,183,189,218,175,164,7,42,69,97,120,204,211]
KW = re.compile(r"(systematic review|meta-analys|randomi[sz]ed|cohort|FAERS|disproportionality|retrospective|trial|review|database|pharmacovigilance study|real-world|incidence)", re.I)
def first_sentence_with_kw(t):
    for s in re.split(r"(?<=[.!?])\s+", t):
        if KW.search(s): return s.strip()[:220]
    return None
rows = []
for i, (pat, ev) in POS.items():
    x = pool[i]
    for q in (pat, ev):
        assert is_verbatim(q, x["abstract"]) or is_verbatim(q, x["title"]), (i, q)
    rows.append({"id": f"P{i:03d}", "drug": x["drug"], "case": True, "title": x["title"], "url": x["url"], "date": x["date"]["raw"],
                 "evidence_patient": pat, "evidence_event": ev, "abstract": x["abstract"], "note": "개별 환자 증례"})
for i in list(NEG_MANUAL) + NEG_AUTO:
    x = pool[i]
    ev = NEG_MANUAL.get(i) or first_sentence_with_kw(x["title"] + ". " + x["abstract"])
    assert ev and (is_verbatim(ev, x["abstract"]) or is_verbatim(ev, x["title"] + ". " + x["abstract"])), (i, ev)
    note = "증례지만 약이 치료로 쓰였거나 이상사례 없음(어려운 음성)" if i in NEG_MANUAL else "개별 증례 아님(연구 설계 근거)"
    if i == 5: note = "동물·세포 연구"
    rows.append({"id": f"N{i:03d}", "drug": x["drug"], "case": False, "title": x["title"], "url": x["url"], "date": x["date"]["raw"],
                 "evidence_design": ev, "abstract": x["abstract"], "note": note})
assert sum(r["case"] for r in rows) == 30 and len(rows) == 60, len(rows)
with open(os.path.join(H, "gold.jsonl"), "w") as f:
    for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
print("gold rows", len(rows))
