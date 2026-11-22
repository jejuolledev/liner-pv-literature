"""모델이 뽑은 문장이 초록(또는 제목)에 글자 그대로 있는지 코드로 확인한다.

허용하는 차이: 띄어쓰기·줄바꿈 수, 대소문자, 유니코드 모양 차이(– — ‐ 를 -, ‘’“” 를 ' ")
그 밖의 차이는 모두 「확인 안 됨」이다. 낱말을 바꾸거나 줄이거나 이어 붙이면 걸린다.
조각은 낱말 경계에서 시작하고 끝나야 한다(「patients」 안의 「pat」은 통과 못 함).
"""
from __future__ import annotations

import re
import unicodedata

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"'}

MIN_QUOTE_CHARS = 3
# 환자 칸에 있어야 할 단서(나이·성별·환자·증례 등). 없으면 「환자 단서 없음」.
_PATIENT_CUE = re.compile(
    r"(\d|year|month|week|old|aged|\bman\b|\bmen\b|woman|women|male|female|boy|girl|child|infant|neonate|"
    r"adolescent|patient|case|individual|person|pregnan|elderly|adult|mother|baby|\bhe\b|\bshe\b|\bhis\b|\bher\b)",
    re.I,
)


def _canon_chars(text: str):
    """원문 글자마다 정규화한 글자들과 원래 위치를 함께 낸다(위치가 밀리지 않게 글자 단위로)."""
    prev_space = True
    for i, ch in enumerate(text or ""):
        n = unicodedata.normalize("NFKC", ch).translate(_DASHES).translate(_QUOTES).lower()
        for c in n:
            if c.isspace():
                if prev_space:
                    continue
                c = " "
                prev_space = True
            else:
                prev_space = False
            yield c, i


def norm(s: str) -> str:
    return "".join(c for c, _ in _canon_chars(s)).strip()


def _w(c: str) -> bool:
    return c.isalnum()


def find_span(quote: str, text: str) -> tuple[int, int] | None:
    """원문 text 안에서 quote 가 차지하는 위치(원문 글자 기준 [시작, 끝))를 돌려준다. 없으면 None."""
    q = norm(quote)
    if len(q) < MIN_QUOTE_CHARS:
        return None
    pairs = list(_canon_chars(text))
    flat = "".join(c for c, _ in pairs)
    start = 0
    while True:
        pos = flat.find(q, start)
        if pos < 0:
            return None
        end = pos + len(q)
        left_ok = pos == 0 or not (_w(flat[pos - 1]) and _w(flat[pos]))
        right_ok = end == len(flat) or not (_w(flat[end - 1]) and _w(flat[end]))
        if left_ok and right_ok:
            return pairs[pos][1], pairs[end - 1][1] + 1
        start = pos + 1


def is_verbatim(quote: str, text: str) -> bool:
    return find_span(quote, text) is not None


def check_quotes(item: dict, title: str, abstract: str, drug_names: list[str]) -> dict:
    """세 칸(환자·의심 약·부작용) 각각의 확인 결과.

    status: ok(원문에 있음) / missing(모델이 비워 둠) / unverified(원문에 없음 또는 조건 미달)
    """
    res = {}
    for key in ("patient_quote", "drug_quote", "event_quote"):
        q = (item.get(key) or "").strip()
        if not q:
            res[key] = {"quote": "", "status": "missing", "where": None, "why": "에이전트가 찾지 못함 · 확인 안 됨"}
            continue
        span_a = find_span(q, abstract or "")
        span_t = None if span_a else find_span(q, title or "")
        if not (span_a or span_t):
            res[key] = {"quote": q, "status": "unverified", "where": None, "why": "초록에서 글자로 확인 안 됨"}
            continue
        status, why = "ok", ""
        nq = norm(q)
        names = [norm(d) for d in drug_names if d]
        if key == "drug_quote":
            if not any(d in nq for d in names):
                status, why = "unverified", "원문에 있지만 이 문장에 성분명이 없음"
            elif nq in names:
                status, why = "unverified", "성분명만 있고 투여 내용이 없음(너무 짧음)"
        elif key == "patient_quote" and not _PATIENT_CUE.search(nq):
            status, why = "unverified", "원문에 있지만 환자를 가리키는 말(나이·성별·환자)이 없음"
        res[key] = {"quote": q, "status": status, "why": why,
                    "where": "abstract" if span_a else "title", "span": span_a or span_t}
    return res
