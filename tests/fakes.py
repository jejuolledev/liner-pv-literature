"""라이너를 부르지 않는 가짜 클라이언트(시험용)."""
from pvlit.liner import Ledger, LinerClient

ABS_CASE = ("We report a 54-year-old woman who developed rhabdomyolysis two weeks after starting atorvastatin 40 mg. "
            "Creatine kinase rose to 12,000 U/L.")
ABS_TRIAL = "In this randomized controlled trial of 400 adults, atorvastatin reduced LDL cholesterol."


class FakeClient(LinerClient):
    def __init__(self, papers=None, answers=None):
        super().__init__(Ledger())
        self.papers = papers if papers is not None else [
            {"title": "Atorvastatin rhabdomyolysis: a case report", "url": "https://doi.org/10.1/abc", "description": ABS_CASE,
             "date": "2026-10-01", "authors": ["A"], "journal": "J"},
            {"title": "Atorvastatin trial", "url": "https://doi.org/10.1/def", "description": ABS_TRIAL,
             "date": "2099-01-01", "authors": [], "journal": "K"},
            {"title": "Unrelated paper", "url": "https://example.org/x", "description": "Nothing about drugs here.",
             "date": None, "authors": [], "journal": ""},
        ]
        self.answers = answers or {}
        self.mark_inputs = []

    def scholar(self, query, max_results=20, date_range="past_year"):
        self.ledger.scholar_calls += 1
        return list(self.papers)

    def mark_json(self, system, user, schema, max_tokens=900):
        self.ledger.mark_calls += 1
        self.mark_inputs.append((system, user))
        for needle, ans in self.answers.items():
            if needle in user:
                return ans
        if "54-year-old woman" in user:
            return {"doc_type": "case_report", "is_individual_case": True, "patient_quote": "a 54-year-old woman",
                    "drug_quote": "starting atorvastatin 40 mg", "event_quote": "developed rhabdomyolysis", "reason_ko": "증례"}
        return {"doc_type": "clinical_trial", "is_individual_case": False, "patient_quote": "", "drug_quote": "",
                "event_quote": "", "reason_ko": "임상시험"}
