"""화면 4개: 작동 방식(/how), 논문 모으기(/), 담당자가 볼 논문(/remaining), 처리 기록(/log). FastAPI 한 개.

실행: LINER_API_KEY=... uvicorn app.main:app --port 8000
"""
from __future__ import annotations

import base64
import csv
import datetime as dt
import hmac
import io
import os
import re
import shutil
import threading
import time
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from pvlit.judge import age_phrase, all_verified
from pvlit.liner import MARK_MODEL
from pvlit.pipeline import KEEP, run_weekly
from pvlit.search import BRAND_TEMPLATE, DEFAULT_DRUGS, QUERY_TEMPLATES, names_for
from pvlit.store import DECISIONS, DEFAULT_DB, Store
from pvlit.verify import check_quotes

HERE = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="약물감시 주간 문헌 검토", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
tpl = Jinja2Templates(directory=os.path.join(HERE, "templates"))


def _prepare_db(path: str) -> str:
    """PV_SEED_DB 가 있고 PV_DB 파일이 아직 없으면 견본 DB를 복사한다(Cloud Run 은 시작할 때마다 빈 /tmp)."""
    seed = os.environ.get("PV_SEED_DB", "")
    if seed and path != ":memory:" and not os.path.exists(path) and os.path.exists(seed):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        shutil.copyfile(seed, path)
    return path


store = Store(_prepare_db(os.environ.get("PV_DB", DEFAULT_DB)))


def safe_url(u: str) -> str:
    """http(s) 주소만 링크로 쓴다(javascript: 같은 주소 막기)."""
    u = (u or "").strip()
    return u if re.match(r"^https?://", u, re.I) else "#"


tpl.env.filters["safe_url"] = safe_url
_run_lock = threading.Lock()
DAILY_LIMIT = int(os.environ.get("PV_DAILY_RUN_LIMIT", "20"))
MAX_JUDGE = int(os.environ.get("PV_MAX_JUDGE_PER_RUN", "150"))  # 한 회차 판정 상한(비용 상한: 약 $0.2)
READONLY = os.environ.get("PV_READONLY") == "1"  # 공개 데모에서 결정·돌리기 막기
REVIEWER = os.environ.get("PV_REVIEWER", "담당자")
# 비공개 운영판 보호: 두 값을 넣으면 모든 화면에 기본 인증(브라우저 로그인 창)을 건다.
BASIC_USER = os.environ.get("PV_BASIC_USER", "")
BASIC_PASS = os.environ.get("PV_BASIC_PASS", "")
tpl.env.globals["readonly"] = READONLY
# 심사위원 직접 실행판 안내 띠(견본이 본 목록에 있다는 것, 서버가 다시 뜨면 결과가 사라진다는 것)
tpl.env.globals["ops_band"] = os.environ.get("PV_OPS_BAND") == "1" and not READONLY
# 처음 화면 입력칸에 미리 넣을 성분(쉼표로). 비우면 견본과 같은 세 성분.
_dd = [d.strip().lower() for d in os.environ.get("PV_DEFAULT_DRUGS", "").split(",") if d.strip()]
REPORT_DAYS = 15  # 의약품 등의 안전에 관한 규칙 별표 4의3 제7호, GVP VI.B.7.1 (docs/law_basis.md)


AUTH_COOKIE = "pv_auth"
AUTH_DAYS = 14


def _auth_token() -> str:
    """로그인 쿠키 값. 비밀번호에서 만들어서 인스턴스가 다시 떠도 같은 값이다(서버에 따로 저장하지 않음)."""
    return hmac.new(BASIC_PASS.encode(), b"pv-login:" + BASIC_USER.encode(), "sha256").hexdigest()


def _authed(request: Request) -> bool:
    tok = request.cookies.get(AUTH_COOKIE, "")
    if tok and hmac.compare_digest(tok, _auth_token()):
        return True
    # 명령줄(curl -u)용으로 기본 인증 머리글도 받는다. 브라우저에는 로그인 창을 띄우지 않는다.
    h = request.headers.get("authorization", "")
    if h.startswith("Basic "):
        try:
            u, _, pw = base64.b64decode(h[6:]).decode("utf-8").partition(":")
            return bool(hmac.compare_digest(u, BASIC_USER) & hmac.compare_digest(pw, BASIC_PASS))
        except Exception:
            return False
    return False


def _safe_next(n: str) -> str:
    """로그인 뒤 돌아갈 곳은 이 사이트 안의 경로만(다른 사이트로 보내지 않음)."""
    return n if n.startswith("/") and not n.startswith("//") and "\\" not in n else "/"


@app.middleware("http")
async def guard(request: Request, call_next):
    """1) 로그인(설정했을 때만) 2) POST 는 다른 사이트에서 보낸 것을 막는다(Origin·Referer 가 있으면 같은 호스트여야).

    로그인은 앱 자체 화면 + 쿠키로 한다. 브라우저 기본 인증(Authorization 머리글)은 Cloud Run 앞단이
    IAM 토큰으로 착각해 「401 Your client does not have permission」으로 막는 일이 있었다(11/22 로그 확인).
    """
    if request.method == "POST":
        # 브라우저는 Sec-Fetch-Site 를 붙인다. 있으면 같은 출처만, 없으면 Origin/Referer 의 호스트를 본다.
        # Origin 이 「null」(샌드박스 iframe·data: 주소)인 것도 막는다.
        sfs = request.headers.get("sec-fetch-site", "")
        src = request.headers.get("origin") or request.headers.get("referer") or ""
        bad = (sfs and sfs not in ("same-origin", "none")) or src == "null" or \
              (src and urlparse(src).netloc != request.headers.get("host", ""))
        if bad:
            return HTMLResponse("다른 사이트에서 보낸 요청은 받지 않습니다.", status_code=403)
    path = request.url.path
    if BASIC_USER and BASIC_PASS and path not in ("/healthz", "/login") and not path.startswith("/static/"):
        if not _authed(request):
            if request.method == "GET" and not path.startswith("/api/") and path != "/log.csv":
                nxt = path + (("?" + request.url.query) if request.url.query else "")
                return RedirectResponse("/login?next=" + quote(nxt, safe=""), status_code=303)
            return JSONResponse({"detail": "로그인이 필요합니다. /login 에서 들어와 주세요."}, status_code=401)
    return await call_next(request)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", bad: int = 0):
    if not (BASIC_USER and BASIC_PASS):
        return RedirectResponse("/", status_code=303)
    return tpl.TemplateResponse(request, "login.html", {"next": _safe_next(next), "bad": bad, "page": "login", "run": None})


@app.post("/login")
def login(request: Request, user: str = Form(""), password: str = Form(""), next: str = Form("/")):
    if not (BASIC_USER and BASIC_PASS):
        return RedirectResponse("/", status_code=303)
    ok = hmac.compare_digest(user.strip(), BASIC_USER) & hmac.compare_digest(password, BASIC_PASS)
    if not ok:
        time.sleep(1)  # 마구 넣어 보기 늦추기
        return RedirectResponse("/login?bad=1&next=" + quote(_safe_next(next), safe=""), status_code=303)
    r = RedirectResponse(_safe_next(next), status_code=303)
    r.set_cookie(AUTH_COOKIE, _auth_token(), max_age=AUTH_DAYS * 86400, httponly=True, samesite="lax",
                 secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https")
    return r


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    """화면에서 생긴 오류는 JSON 대신 알아볼 수 있는 화면으로(돌아가기 단추 포함). /api 는 JSON 그대로."""
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    msg = exc.detail if isinstance(exc.detail, str) and exc.detail not in ("Not Found", "Bad Request") else \
        {404: "찾는 화면이나 논문이 없습니다.", 400: "입력한 값을 확인해 주세요."}.get(exc.status_code, "요청을 처리하지 못했습니다.")
    return tpl.TemplateResponse(request, "error.html", {"msg": msg, "code": exc.status_code, "page": "", "run": None},
                                status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def form_error(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "입력한 값을 확인해 주세요."}, status_code=422)
    return tpl.TemplateResponse(request, "error.html", {"msg": "입력한 값을 확인해 주세요.", "code": 422, "page": "", "run": None},
                                status_code=422)


@app.get("/healthz")
def healthz():
    return {"ok": True, "readonly": READONLY}


_DRUG_RE = re.compile(r"^[A-Za-z][A-Za-z\- ]{2,39}$")

FIELD_LABEL = {"patient_quote": "환자", "drug_quote": "의심 약", "event_quote": "부작용"}
STATUS_KO = {"ok": "확인됨", "unverified": "확인 안 됨", "missing": "못 찾음"}
# 옛 회차(견본 DB)에 저장된 진행 단계 이름을 새 화면 말로 바꿔 보여 준다.
PHASE_KO = {"검색 중": "논문 찾는 중", "판정 중": "AI가 읽는 중", "끝": "끝남"}
tpl.env.globals["phase_ko"] = lambda ph: PHASE_KO.get(ph or "", ph or "")
NO_DRUG_REASON = "제목과 초록에 성분명이 없어 AI에게 보내지 않고 코드가 뺐습니다. 다음 검토에서 다시 확인합니다."


def why_kept(p: dict, j: dict, all_ok: bool) -> str:
    """카드가 담당자 목록에 남은 까닭을 한 줄로(pvlit.judge.verdict 와 같은 조건)."""
    if p["verdict"] == "error":
        return f"AI가 판정하지 못해서 남겼습니다({j.get('error') or '이유 없음'}). 초록을 직접 읽어 주세요."
    m = j.get("model") or {}
    if p["verdict"] == "check":
        why = []
        if j.get("injection"):
            why.append("초록에 AI에게 내리는 지시처럼 보이는 글이 있음")
        if j.get("age_cue"):
            ph = age_phrase(p.get("title") or "", p.get("abstract") or "")
            why.append(f"초록에 환자 나이 표현({ph})이 있음" if ph else "초록에 환자 나이 표현이 있음")
        if m.get("doc_type") in ("case_report", "case_series"):
            why.append("AI가 문서 종류를 증례 보고로 보고도 증례가 아니라고 함")
        return "AI는 증례가 아니라고 했지만 사람이 확인하도록 남겼습니다. 까닭: " + ", ".join(why or ["판정이 미심쩍음"]) + "."
    base = "AI가 환자, 의심 약, 부작용이 나오는 개별 증례로 판정했습니다."
    return base + ("" if all_ok else " 다만 근거 칸 일부가 초록 원문에서 확인되지 않았으니 원문을 직접 봐 주세요.")


DEFAULT_RUN = int(os.environ.get("PV_DEFAULT_RUN", "0") or 0)  # 견본판: 처음 보여 줄 회차(0이면 최신)


def _run_or_latest(run: int | None):
    if not run and DEFAULT_RUN:
        run = DEFAULT_RUN
    r = (store.get_run(run) if run else None) or store.latest_run()
    return r


def highlight(text: str, spans: list[tuple[int, int, str]]) -> Markup:
    """초록에서 확인된 문장 위치만 칠한다. 모든 글은 escape 한 뒤 붙인다."""
    spans = sorted([s for s in spans if s], key=lambda s: s[0])
    out, pos = [], 0
    for a, b, cls in spans:
        if a < pos:
            continue
        out.append(str(escape(text[pos:a])))
        out.append(f'<mark class="{cls}">{escape(text[a:b])}</mark>')
        pos = b
    out.append(str(escape(text[pos:])))
    return Markup("".join(out))


def card(p: dict, started: str) -> dict:
    j = p["judgment"] or {}
    if j.get("model"):  # 화면에 낼 때마다 원문 대조를 다시 한다(저장된 표시를 믿지 않음)
        j["checks"] = check_quotes(j["model"], p["title"], p["abstract"], names_for(p["drug"]))
    checks = j.get("checks") or {}
    spans_a, spans_t = [], []
    fields = []
    for k in ("patient_quote", "drug_quote", "event_quote"):
        c = checks.get(k) or {"status": "missing", "quote": ""}
        fields.append({"label": FIELD_LABEL[k], **c})
        if c.get("status") == "ok" and c.get("span"):
            (spans_a if c["where"] == "abstract" else spans_t).append((c["span"][0], c["span"][1], k))
    found = dt.date.fromisoformat(started[:10])
    ok = all_verified(j)
    return {
        **p,
        "why_kept": why_kept(p, j, ok),
        "fields": fields,
        "all_ok": ok,
        "abstract_html": highlight(p["abstract"] or "", spans_a),
        "title_html": highlight(p["title"] or "", spans_t),
        "reason": (j.get("model") or {}).get("reason_ko") or j.get("error") or "",
        "doc_type": (j.get("model") or {}).get("doc_type") or "",
        "injection": j.get("injection") or [],
        "found": found.isoformat(),
        "due": (found + dt.timedelta(days=REPORT_DAYS)).isoformat(),
    }


@app.get("/", response_class=HTMLResponse)
def home(request: Request, run: int | None = None):
    r = _run_or_latest(run)
    # 다음에 누를 곳 한 군데(숨쉬는 테두리)와 「다음」 한 줄
    if r and r["status"] == "running":
        focus, nxt = "", "AI가 논문을 읽는 중입니다. 끝나면 「담당자가 볼 논문 보기」 단추로 넘어가 주세요."
    elif r and (READONLY or (r["stats"] or {}).get("new")):
        focus, nxt = "go", f"{r['id']}회차 검토가 끝났습니다. 「담당자가 볼 논문 보기」를 눌러 AI가 남긴 논문을 확인해 주세요."
    else:
        focus, nxt = "run", "성분명을 넣고 「논문 찾아서 읽기」를 눌러 주세요. 처음 보는 성분이면 AI가 새 논문을 읽는 모습을 볼 수 있습니다."
    return tpl.TemplateResponse(request, "home.html", {
        "focus": focus, "next_hint": nxt,
        "run": r, "drugs": (_dd or list(DEFAULT_DRUGS))[:3], "limit": DAILY_LIMIT, "today": store.runs_today(),
        "seen": store.seen_count(), "page": "home", "readonly": READONLY, "max_judge": MAX_JUDGE,
    })


@app.get("/how", response_class=HTMLResponse)
def how(request: Request, run: int | None = None):
    """작동 방식. 흐름 그림의 숫자는 실제 회차 기록에서 가져온다(새로 읽은 논문이 있는 가장 최근 회차)."""
    r = store.get_run(run) if run else None
    if not r or not (r["stats"] or {}).get("new"):
        r = next((x for x in store.runs() if (x["stats"] or {}).get("new")), None)
    return tpl.TemplateResponse(request, "how.html", {
        "flow": r, "page": "how", "run": None, "days": REPORT_DAYS,
        "queries": [t.format(d="성분명") for t in QUERY_TEMPLATES], "brand_query": BRAND_TEMPLATE.format(b="상품명"),
    })


@app.post("/run")
def start_run(drug1: str = Form(""), drug2: str = Form(""), drug3: str = Form("")):
    names = [d.strip().lower() for d in (drug1, drug2, drug3) if d.strip()]
    if not names:
        raise HTTPException(400, "성분명을 하나 이상 넣어 주세요.")
    for d in names:
        if not _DRUG_RE.match(d):
            raise HTTPException(400, f"「{d}」는 쓸 수 없습니다. 성분명은 영문 3~40자(글자·띄어쓰기·하이픈)로 넣어 주세요. 예: clozapine")
    if READONLY:
        raise HTTPException(403, "견본 화면이라 돌리기가 꺼져 있습니다. 직접 돌리려면 README의 실행 방법대로 라이너 키를 넣고 띄워 주세요.")
    if store.runs_today() >= DAILY_LIMIT:
        raise HTTPException(429, f"오늘 돌릴 수 있는 {DAILY_LIMIT}번을 다 썼습니다. 이미 돌린 결과는 「담당자가 볼 논문」과 「처리 기록」에서 볼 수 있습니다.")
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(409, "지금 다른 검토가 돌고 있습니다. 한 번에 하나만 돌 수 있습니다. 「논문 모으기」 화면에서 진행 상황을 볼 수 있으니 끝난 뒤 다시 눌러 주세요.")
    try:
        drugs = {d: names_for(d) for d in dict.fromkeys(names)}
        run_id = store.new_run(list(drugs))

        def go():
            try:
                run_weekly(store, drugs, run_id=run_id, max_judge=MAX_JUDGE)
            finally:
                _run_lock.release()

        threading.Thread(target=go, daemon=True).start()
    except Exception:
        _run_lock.release()
        raise
    return RedirectResponse(f"/?run={run_id}", status_code=303)


@app.get("/api/run/{run_id}")
def run_status(run_id: int):
    r = store.get_run(run_id)
    if not r:
        raise HTTPException(404)
    return JSONResponse({k: r[k] for k in ("id", "status", "stats", "ledger", "drugs")} | {"log": r["log"][-6:]})


@app.get("/remaining", response_class=HTMLResponse)
def remaining(request: Request, run: int | None = None):
    """run 을 주지 않으면 모든 회차에서 아직 결정하지 않은(또는 「나중에」) 카드. 회차를 바꿔도 사라지지 않는다."""
    r = _run_or_latest(run)
    cards, dropped = [], 0
    if run and r:
        cards = [card(p, r["started"]) for p in store.papers(r["id"], KEEP)]
    elif r:
        cards = [card(p, p["run_started"]) for p in store.open_cards(KEEP)]
    if r:
        dropped = len(store.papers(r["id"], ("not_case", "no_drug_name")))
    order = {"error": 0, "check": 1, "candidate": 2}
    cards.sort(key=lambda c: (c["decision"] not in (None, "later"), c["found"], c["all_ok"], order.get(c["verdict"], 3), c["drug"]))
    open_i = next((i for i, c in enumerate(cards) if not c["decision"]), None)
    if READONLY:
        focus, nxt = "log", "견본 화면이라 결정 단추는 꺼져 있습니다. 카드에서 「왜 남았나」와 노란 칸을 읽어 본 뒤, 뺀 논문은 「처리 기록 보기」에서 확인해 주세요."
    elif open_i is not None:
        focus, nxt = f"card{open_i}", "맨 위 카드부터 「왜 남았나」와 노란 칸을 읽고 「보고 대상」 또는 「보고 대상 아님」을 눌러 주세요."
    else:
        focus, nxt = "log", "모든 카드를 정했습니다. 「처리 기록 보기」에서 결정을 확인하고 CSV로 내려받아 주세요."
    return tpl.TemplateResponse(request, "remaining.html", {
        "focus": focus, "next_hint": nxt if r else "",
        "run": r, "cards": cards, "decisions": DECISIONS, "page": "remaining", "days": REPORT_DAYS, "dropped": dropped,
        "all_runs": not run, "readonly": READONLY,
    })


@app.post("/decide")
def decide(run_id: int = Form(...), drug: str = Form(...), key: str = Form(...), decision: str = Form(...),
           note: str = Form(""), back: str = Form("")):
    if READONLY:
        raise HTTPException(403, "견본 화면이라 결정 단추가 꺼져 있습니다.")
    if decision not in DECISIONS:
        raise HTTPException(400)
    if not store.get_paper(run_id, drug, key):
        raise HTTPException(404)
    store.decide(run_id, drug, key, decision, note, REVIEWER)
    return RedirectResponse(f"/remaining?run={run_id}" if back == "run" else "/remaining", status_code=303)


VERDICT_KO = {"candidate": "남김: 증례", "check": "남김: 확인 필요", "not_case": "뺌: 증례 아님",
              "no_drug_name": "뺌: 성분명 없음", "error": "남김: AI 판정 실패"}


def log_reason(p: dict) -> str:
    """처리 기록의 「남기거나 뺀 이유」. AI가 적은 한 줄 앞에 무엇을 했는지 붙인다."""
    j = p["judgment"] or {}
    if p["verdict"] == "no_drug_name":
        return NO_DRUG_REASON
    ai = (j.get("model") or {}).get("reason_ko") or ""
    if p["verdict"] == "not_case":
        return "AI가 개별 증례가 아니라고 판정했습니다." + (f" AI가 적은 이유: {ai}" if ai else "")
    if p["verdict"] == "error":
        return f"AI가 판정하지 못해 담당자 목록에 남겼습니다({j.get('error') or j.get('reason') or '이유 없음'})."
    return why_kept(p, j, all_verified(j)) + (f" AI가 적은 이유: {ai}" if ai else "")


@app.get("/log", response_class=HTMLResponse)
def log(request: Request, run: int | None = None):
    r = _run_or_latest(run)
    rows = store.papers(r["id"]) if r else []
    if r and not rows and not run:  # 기본으로 여는 회차가 비었으면 기록이 있는 가장 최근 회차를 연다
        for x in store.runs():
            if store.papers(x["id"]):
                r, rows = x, store.papers(x["id"])
                break
    for p in rows:
        j = p["judgment"] or {}
        if j.get("model"):
            j["checks"] = check_quotes(j["model"], p["title"], p["abstract"], names_for(p["drug"]))
        p["reason"] = log_reason(p)
        p["verdict_ko"] = VERDICT_KO.get(p["verdict"], p["verdict"])
    return tpl.TemplateResponse(request, "log.html", {
        "focus": "csv", "next_hint": "결정까지 끝났으면 「CSV 내려받기」로 이번 검토 기록을 받아 두세요. 감사 때 그대로 낼 수 있습니다." if r else "",
        "run": r, "rows": rows, "runs": store.runs()[:20], "page": "log", "decisions": DECISIONS,
    })


class _SafeWriter:
    """엑셀에서 수식으로 실행되지 않게 =,+,-,@ 로 시작하는 칸 앞에 ' 를 붙인다(CSV 수식 주입 막기)."""

    def __init__(self, w):
        self.w = w

    def writerow(self, row):
        self.w.writerow(["'" + c if isinstance(c, str) and c.lstrip(" ")[:1] in ("=", "+", "-", "@", "\t", "\r") else c for c in row])


@app.get("/log.csv")
def log_csv(run: int):
    r = store.get_run(run)
    if not r:
        raise HTTPException(404)
    buf = io.StringIO()
    buf.write("﻿")
    w = _SafeWriter(csv.writer(buf))
    w.writerow(["검색일", "성분", "제목", "주소", "발행일(라이너 표기)", "날짜 표시", "처리", "남기거나 뺀 이유",
                "환자 문장", "의심 약 문장", "부작용 문장", "원문 대조", "담당자 결정", "결정 시각", "메모", "찾은 검색어",
                "검색 범위", "판정 모델", "프롬프트 버전", "초록 원문"])
    for p in store.papers(r["id"]):
        j = p["judgment"] or {}
        c = j.get("checks") or {}
        q = lambda k: (c.get(k) or {}).get("quote", "")
        st = ", ".join(f"{FIELD_LABEL[k]} {STATUS_KO.get((c.get(k) or {}).get('status'), '-')}" for k in FIELD_LABEL)
        w.writerow([r["started"][:10], p["drug"], p["title"], p["url"], (p["date"] or {}).get("raw"),
                    (p["date"] or {}).get("flag") or "", VERDICT_KO.get(p["verdict"], p["verdict"]),
                    log_reason(p),
                    q("patient_quote"), q("drug_quote"), q("event_quote"), st if c else "",
                    DECISIONS.get(p["decision"], ""), p["decided_at"] or "", p["note"] or "", " / ".join(p["queries"] or []),
                    "라이너 학술 검색 past_year", j.get("model_name") or (MARK_MODEL if j.get("model") else ""),
                    j.get("prompt_version") or "", p["abstract"] or ""])
    data = buf.getvalue().encode("utf-8")
    return StreamingResponse(iter([data]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="pv_review_run{run}.csv"'})
