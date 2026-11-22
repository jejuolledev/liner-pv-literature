"""화면 3개: 이번 주 검토(/), 남은 것(/remaining), 기록(/log). FastAPI 한 개.

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

from pvlit.judge import all_verified
from pvlit.liner import MARK_MODEL
from pvlit.pipeline import KEEP, run_weekly
from pvlit.search import DEFAULT_DRUGS, names_for
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
        {404: "찾는 화면이나 논문이 없습니다.", 400: "입력이 올바르지 않습니다."}.get(exc.status_code, "요청을 처리하지 못했습니다.")
    return tpl.TemplateResponse(request, "error.html", {"msg": msg, "code": exc.status_code, "page": "", "run": None},
                                status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def form_error(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "입력이 올바르지 않습니다."}, status_code=422)
    return tpl.TemplateResponse(request, "error.html", {"msg": "입력이 올바르지 않습니다.", "code": 422, "page": "", "run": None},
                                status_code=422)


@app.get("/healthz")
def healthz():
    return {"ok": True, "readonly": READONLY}


_DRUG_RE = re.compile(r"^[A-Za-z][A-Za-z\- ]{2,39}$")

FIELD_LABEL = {"patient_quote": "환자", "drug_quote": "의심 약", "event_quote": "부작용"}


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
    return {
        **p,
        "fields": fields,
        "all_ok": all_verified(j),
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
    return tpl.TemplateResponse(request, "home.html", {
        "run": r, "drugs": (_dd or list(DEFAULT_DRUGS))[:3], "limit": DAILY_LIMIT, "today": store.runs_today(),
        "seen": store.seen_count(), "page": "home", "readonly": READONLY, "max_judge": MAX_JUDGE,
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
        raise HTTPException(403, "읽기 전용 데모입니다.")
    if store.runs_today() >= DAILY_LIMIT:
        raise HTTPException(429, f"오늘 돌릴 수 있는 {DAILY_LIMIT}번을 다 썼습니다. 이미 돌린 결과는 「남은 것」「기록」에서 볼 수 있습니다.")
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(409, "지금 다른 검토가 돌고 있습니다(한 번에 하나만 돕니다). 첫 화면에서 진행을 볼 수 있고, 끝나면 1~2분 뒤 다시 눌러 주세요.")
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
    return tpl.TemplateResponse(request, "remaining.html", {
        "run": r, "cards": cards, "decisions": DECISIONS, "page": "remaining", "days": REPORT_DAYS, "dropped": dropped,
        "all_runs": not run, "readonly": READONLY,
    })


@app.post("/decide")
def decide(run_id: int = Form(...), drug: str = Form(...), key: str = Form(...), decision: str = Form(...),
           note: str = Form(""), back: str = Form("")):
    if READONLY:
        raise HTTPException(403, "읽기 전용 데모입니다.")
    if decision not in DECISIONS:
        raise HTTPException(400)
    if not store.get_paper(run_id, drug, key):
        raise HTTPException(404)
    store.decide(run_id, drug, key, decision, note, REVIEWER)
    return RedirectResponse(f"/remaining?run={run_id}" if back == "run" else "/remaining", status_code=303)


VERDICT_KO = {"candidate": "남김(증례 후보)", "check": "남김(확인 필요)", "not_case": "뺌(증례 아님)",
              "no_drug_name": "뺌(성분명 없음, 다음 주 다시 봄)", "error": "남김(판정 못 함)"}


@app.get("/log", response_class=HTMLResponse)
def log(request: Request, run: int | None = None):
    r = _run_or_latest(run)
    rows = store.papers(r["id"]) if r else []
    for p in rows:
        j = p["judgment"] or {}
        p["reason"] = (j.get("model") or {}).get("reason_ko") or j.get("reason") or j.get("error") or ""
        p["verdict_ko"] = VERDICT_KO.get(p["verdict"], p["verdict"])
    return tpl.TemplateResponse(request, "log.html", {
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
    w.writerow(["검색일", "성분", "제목", "주소", "발행일(라이너 표기)", "날짜 표시", "에이전트 판단", "이유",
                "환자 문장", "의심 약 문장", "부작용 문장", "원문 확인", "사람 결정", "결정 시각", "메모", "찾은 질의",
                "검색 범위", "판정 모델", "프롬프트 버전", "초록 원문"])
    for p in store.papers(r["id"]):
        j = p["judgment"] or {}
        c = j.get("checks") or {}
        q = lambda k: (c.get(k) or {}).get("quote", "")
        st = ",".join(f"{FIELD_LABEL[k]}:{(c.get(k) or {}).get('status', '-')}" for k in FIELD_LABEL)
        w.writerow([r["started"][:10], p["drug"], p["title"], p["url"], (p["date"] or {}).get("raw"),
                    (p["date"] or {}).get("flag") or "", VERDICT_KO.get(p["verdict"], p["verdict"]),
                    (j.get("model") or {}).get("reason_ko") or j.get("reason") or j.get("error") or "",
                    q("patient_quote"), q("drug_quote"), q("event_quote"), st if c else "",
                    DECISIONS.get(p["decision"], ""), p["decided_at"] or "", p["note"] or "", " / ".join(p["queries"] or []),
                    "라이너 학술 검색 past_year", j.get("model_name") or (MARK_MODEL if j.get("model") else ""),
                    j.get("prompt_version") or "", p["abstract"] or ""])
    data = buf.getvalue().encode("utf-8")
    return StreamingResponse(iter([data]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="pv_review_run{run}.csv"'})
