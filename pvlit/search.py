"""라이너 학술 검색으로 성분명마다 논문을 모은다.

「이번 주」는 검색 날짜 옵션으로 자르지 않는다(11/19 시험: 「지난 주」로 자르면 성분명이 든 결과가 19건 중 0~2건).
1년 범위로 여러 질의를 돌리고, 지난번까지 본 목록을 빼서 새 논문만 남긴다.
"""
from __future__ import annotations

import datetime as dt
import re

from .liner import LinerClient, LinerError, MissingKey
from .verify import norm

QUERY_TEMPLATES = [
    "{d} case report",
    "{d}-induced adverse reaction in a patient",
    "{d} toxicity case",
    "{d} associated adverse event patient",
    # 11/19 추가: 지난 한 주(검토 #1)에 없던 증례 후보 3편을 더 찾음(eval/results/query_compare_*.json).
    "{d} case series adverse reaction",
]
# 상품명 질의(이름 칸의 둘째·셋째 이름). 11/19 측정: Ozempic 질의에서 새 후보 1편.
BRAND_TEMPLATE = "{b} case report"
MAX_BRAND_QUERIES = 2

# 공개 약 세 개(데모 기본값). 이름 칸은 성분명과 영문 상품명.
DEFAULT_DRUGS = {
    "atorvastatin": ["atorvastatin", "Lipitor"],
    "semaglutide": ["semaglutide", "Ozempic", "Wegovy", "Rybelsus"],
    "metformin": ["metformin", "Glucophage"],
}
# 이름 칸 첫째는 영문 성분명(INN), 그다음 상품명·다른 나라 표기. 원문 대조와 「성분명 포함」 검사에 쓴다.

# 정답표 성분까지 포함한 이름 목록(원문 대조·성분명 포함 여부에 쓴다).
KNOWN_NAMES = {
    **{d: list(n) for d, n in DEFAULT_DRUGS.items()},
    "pembrolizumab": ["pembrolizumab", "Keytruda"],
    "vancomycin": ["vancomycin", "Vancocin"],
    # 「amiodaron」은 독일어·베트남어 표기, Cordaron 은 베트남 상품명(11/19 어려운 시험지 P151).
    "amiodarone": ["amiodarone", "Cordarone", "Cordaron", "Pacerone", "amiodaron", "amiodarona"],
    "levetiracetam": ["levetiracetam", "Keppra"],
    "isotretinoin": ["isotretinoin", "Accutane", "Roaccutane", "Absorica"],
}


def names_for(drug: str) -> list[str]:
    return KNOWN_NAMES.get(drug.lower(), [drug])


_DOI = re.compile(r"10\.\d{4,9}/\S+", re.I)


def paper_key(r: dict) -> str:
    """같은 논문을 하나로 묶는 열쇠: DOI 가 있으면 DOI, 없으면 주소, 없으면 제목."""
    url = (r.get("url") or "").strip()
    m = _DOI.search(url)
    if m:
        return "doi:" + m.group(0).lower().rstrip(".")
    if url:
        return "url:" + url.lower().split("#")[0].rstrip("/")
    return "title:" + norm(r.get("title") or "")


def clean_date(raw: str | None, today: dt.date | None = None) -> dict:
    """라이너가 준 날짜를 그대로 보여 주되, 읽을 수 없거나 미래면 표시한다."""
    today = today or dt.date.today()
    if not raw:
        return {"raw": None, "date": None, "flag": "날짜 없음"}
    try:
        d = dt.date.fromisoformat(str(raw)[:10])
    except ValueError:
        return {"raw": raw, "date": None, "flag": "날짜를 읽지 못함"}
    if d > today:
        return {"raw": raw, "date": d.isoformat(), "flag": "미래 날짜(라이너 표기 그대로, 확인 필요)"}
    return {"raw": raw, "date": d.isoformat(), "flag": None}


def mentions_drug(r: dict, names: list[str]) -> bool:
    text = norm((r.get("title") or "") + " " + (r.get("description") or ""))
    return any(norm(n) in text for n in names if n)


# 상품명 질의에 쓸 이름(다른 나라 성분명 표기는 빼고, 상품명만 명시). 없으면 이름 칸 둘째부터.
# 데모용 목록이다. 실제 회사 제품은 이름 사전(성분명·상품명·다른 표기)을 따로 받아야 한다(다음 할 일).
BRANDS = {
    "atorvastatin": ["Lipitor"], "semaglutide": ["Ozempic", "Wegovy", "Rybelsus"], "metformin": ["Glucophage"],
    "pembrolizumab": ["Keytruda"], "vancomycin": ["Vancocin"], "amiodarone": ["Cordarone", "Pacerone"],
    "levetiracetam": ["Keppra"], "isotretinoin": ["Accutane", "Roaccutane", "Absorica"],
}


def queries_for(drug: str, names: list[str], templates=None, brands: bool = True) -> list[str]:
    qs = [t.format(d=drug) for t in (templates or QUERY_TEMPLATES)]
    if brands:
        pool = BRANDS.get(drug.lower(), names[1:])
        uniq: list[str] = []
        for b in pool:
            if norm(b) != norm(drug) and norm(b) not in {norm(u) for u in uniq}:
                uniq.append(b)
        qs += [BRAND_TEMPLATE.format(b=b) for b in uniq[:MAX_BRAND_QUERIES]]
    return qs


def collect(client: LinerClient, drug: str, names: list[str], templates=None, max_results: int = 20,
            progress=None, failures: list | None = None, brands: bool = True) -> list[dict]:
    """한 성분에 대해 질의를 돌리고 중복을 뺀 논문 목록을 돌려준다. 실패한 질의는 failures 에 넣는다.
    라이너 학술 검색에는 문서 종류 필터가 없어서(11/19 확인한 요청 칸: query·max_results·date_range) 질의 글로 증례 쪽을 고른다."""
    seen: dict[str, dict] = {}
    for q in queries_for(drug, names, templates, brands):
        try:
            results = client.scholar(q, max_results=max_results, date_range="past_year")
        except MissingKey:
            raise
        except LinerError as e:
            if failures is not None:
                failures.append(q)
            if progress:
                progress(f"검색 실패: {q} ({str(e)[:60]})")
            continue
        for r in results:
            k = paper_key(r)
            if k in seen:
                seen[k]["queries"].append(q)
                continue
            seen[k] = {
                "key": k,
                "drug": drug,
                "title": r.get("title") or "",
                "abstract": r.get("description") or "",
                "url": r.get("url") or "",
                "date": clean_date(r.get("date")),
                "authors": r.get("authors") or [],
                "journal": r.get("journal") or "",
                "queries": [q],
                "mentions_drug": mentions_drug(r, names),
            }
        if progress:
            progress(f"검색: {q} → {len(results)}편")
    return list(seen.values())
