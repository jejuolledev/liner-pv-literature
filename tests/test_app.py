import os, importlib
import pytest
from fastapi.testclient import TestClient

@pytest.fixture()
def app_mod(tmp_path, monkeypatch):
    monkeypatch.setenv("PV_DB", str(tmp_path / "a.sqlite3"))
    import pvlit.store as s; importlib.reload(s)
    import app.main as m; importlib.reload(m)
    return m

def seed(m, title, url, abstract):
    from pvlit.judge import judge_paper, verdict
    from tests.fakes import FakeClient
    rid = m.store.new_run(["atorvastatin"])
    p = {"key": "doi:1", "drug": "atorvastatin", "title": title, "abstract": abstract, "url": url,
         "date": {"raw": "2026-10-01", "date": "2026-10-01", "flag": None}, "authors": [], "journal": "J", "queries": ["q"]}
    j = judge_paper(FakeClient(), p, "atorvastatin")
    m.store.save_paper(rid, p, verdict(j), j)
    m.store.update_run(rid, status="done")
    return rid

def test_pages_render_and_escape(app_mod):
    from tests.fakes import ABS_CASE
    rid = seed(app_mod, "=HYPERLINK(\"x\") <script>alert(1)</script>", "javascript:alert(1)", ABS_CASE)
    c = TestClient(app_mod.app)
    for path in ("/", "/remaining", "/log"):
        r = c.get(path); assert r.status_code == 200
    html = c.get("/remaining").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert 'href="javascript:' not in html
    assert '<mark class="patient_quote">a 54-year-old woman</mark>' in html
    assert "2026-" in html and "보고 대상" in html
    csv = c.get(f"/log.csv?run={rid}").content.decode("utf-8-sig")
    assert "'=HYPERLINK" in csv

def test_decide_and_validation(app_mod):
    from tests.fakes import ABS_CASE
    rid = seed(app_mod, "t", "https://doi.org/10.1/a", ABS_CASE)
    c = TestClient(app_mod.app)
    r = c.post("/decide", data={"run_id": rid, "drug": "atorvastatin", "key": "doi:1", "decision": "report"}, follow_redirects=False)
    assert r.status_code == 303
    assert app_mod.store.get_paper(rid, "atorvastatin", "doi:1")["decision"] == "report"
    assert c.post("/decide", data={"run_id": rid, "drug": "atorvastatin", "key": "doi:1", "decision": "hack"}).status_code == 400
    assert c.post("/run", data={"drug1": "<script>"}).status_code == 400

def test_run_without_key_fails_cleanly(app_mod, monkeypatch):
    monkeypatch.delenv("LINER_API_KEY", raising=False)
    from pvlit.pipeline import run_weekly
    rid = run_weekly(app_mod.store, {"atorvastatin": ["atorvastatin"]})
    run = app_mod.store.get_run(rid)
    assert run["status"] == "failed"
    assert "키" in run["log"][-1]

def test_no_key_value_in_repo():
    key = os.environ.get("LINER_API_KEY") or ""
    if not key and os.path.exists(os.path.expanduser("~/.liner_exethon_key")):
        key = open(os.path.expanduser("~/.liner_exethon_key")).read().strip()
    if not key: pytest.skip("키 없음")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in (".venv", ".git", "__pycache__")]
        for fn in fns:
            p = os.path.join(dp, fn)
            try: data = open(p, "rb").read()
            except OSError: continue
            assert key.encode() not in data, p


def test_open_cards_survive_new_run(app_mod):
    from tests.fakes import ABS_CASE
    rid = seed(app_mod, "t", "https://doi.org/10.1/a", ABS_CASE)
    app_mod.store.update_run(app_mod.store.new_run(["atorvastatin"]), status="done")  # 새 회차(빈)
    c = TestClient(app_mod.app)
    html = c.get("/remaining").text
    assert "남은 것 1편" in html
    c.post("/decide", data={"run_id": rid, "drug": "atorvastatin", "key": "doi:1", "decision": "later"})
    assert "남은 것 1편" in c.get("/remaining").text  # 나중에 는 남는다
    c.post("/decide", data={"run_id": rid, "drug": "atorvastatin", "key": "doi:1", "decision": "not_report", "note": "-x"})
    assert "남은 것 0편" in c.get("/remaining").text
    assert len(app_mod.store.decision_history(rid, "atorvastatin", "doi:1")) == 2

def test_csv_tab_prefix_and_limits(app_mod, monkeypatch):
    from tests.fakes import ABS_CASE
    rid = seed(app_mod, "\t=cmd", "https://doi.org/10.1/a", ABS_CASE)
    c = TestClient(app_mod.app)
    assert "'\t=cmd" in c.get(f"/log.csv?run={rid}").content.decode("utf-8-sig")
    monkeypatch.setattr(app_mod, "DAILY_LIMIT", 0)
    assert c.post("/run", data={"drug1": "atorvastatin"}).status_code == 429
    monkeypatch.setattr(app_mod, "DAILY_LIMIT", 99)
    app_mod._run_lock.acquire()
    try:
        assert c.post("/run", data={"drug1": "atorvastatin"}).status_code == 409
    finally:
        app_mod._run_lock.release()

def test_log_page_escapes(app_mod):
    from tests.fakes import ABS_CASE
    seed(app_mod, "<img src=x onerror=alert(1)>", "https://doi.org/10.1/a", ABS_CASE)
    html = TestClient(app_mod.app).get("/log").text
    assert "<img src=x" not in html and "&lt;img" in html


def _fresh(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("PV_DB", str(tmp_path / "b.sqlite3"))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import pvlit.store as s; importlib.reload(s)
    import app.main as m; importlib.reload(m)
    return m


def test_readonly_blocks_writes_and_shows_band(tmp_path, monkeypatch):
    m = _fresh(monkeypatch, tmp_path, PV_READONLY="1")
    c = TestClient(m.app)
    assert c.post("/run", data={"drug1": "atorvastatin"}).status_code == 403
    assert c.post("/decide", data={"run_id": 1, "drug": "a", "key": "k", "decision": "report"}).status_code == 403
    assert "견본 화면입니다" in c.get("/").text
    assert c.get("/healthz").json() == {"ok": True, "readonly": True}


def test_seed_db_copied_once(tmp_path, monkeypatch):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    seed_db = os.path.join(root, "seed", "pv_demo.sqlite3")
    if not os.path.exists(seed_db): pytest.skip("견본 DB 없음")
    m = _fresh(monkeypatch, tmp_path, PV_SEED_DB=seed_db, PV_READONLY="1", PV_DEFAULT_RUN="1")
    c = TestClient(m.app)
    assert len(m.store.runs()) == 2
    assert "검토 #1" in c.get("/").text
    assert c.get("/remaining").status_code == 200 and c.get("/log").status_code == 200


def test_basic_auth_and_cross_site_post(tmp_path, monkeypatch):
    import base64
    m = _fresh(monkeypatch, tmp_path, PV_BASIC_USER="u", PV_BASIC_PASS="p")
    c = TestClient(m.app, follow_redirects=False)
    assert c.get("/").status_code == 303
    assert c.get("/healthz").status_code == 200
    good = {"authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    bad = {"authorization": "Basic " + base64.b64encode(b"u:x").decode()}
    assert c.get("/", headers=bad).status_code == 303
    assert c.get("/", headers=good).status_code == 200
    r = c.post("/decide", headers={**good, "origin": "https://evil.example"},
               data={"run_id": 1, "drug": "a", "key": "k", "decision": "report"})
    assert r.status_code == 403


def test_null_origin_and_cross_site_fetch_blocked(tmp_path, monkeypatch):
    m = _fresh(monkeypatch, tmp_path)
    c = TestClient(m.app)
    d = {"run_id": 1, "drug": "a", "key": "k", "decision": "report"}
    assert c.post("/decide", headers={"origin": "null"}, data=d).status_code == 403
    assert c.post("/decide", headers={"sec-fetch-site": "cross-site"}, data=d).status_code == 403
    assert c.post("/decide", headers={"sec-fetch-site": "same-origin"}, data=d).status_code == 404  # 통과해서 「없는 카드」


def test_decide_from_all_runs_view_uses_card_run(app_mod):
    """모든 회차 모아보기에서 옛 회차 카드를 결정해도 그 카드의 회차로 저장된다."""
    from tests.fakes import ABS_CASE
    rid = seed(app_mod, "t", "https://doi.org/10.1/a", ABS_CASE)
    app_mod.store.update_run(app_mod.store.new_run(["atorvastatin"]), status="done")  # 새 회차(빈 것)
    c = TestClient(app_mod.app)
    html = c.get("/remaining").text
    assert f'name="run_id" value="{rid}"' in html
    r = c.post("/decide", data={"run_id": rid, "drug": "atorvastatin", "key": "doi:1", "decision": "later"}, follow_redirects=False)
    assert r.status_code == 303


def test_readonly_buttons_disabled_and_unknown_run_falls_back(tmp_path, monkeypatch):
    from tests.fakes import ABS_CASE
    m = _fresh(monkeypatch, tmp_path, PV_READONLY="1", PV_DEFAULT_RUN="99")
    rid = seed(m, "t", "https://doi.org/10.1/a", ABS_CASE)
    c = TestClient(m.app)
    html = c.get("/remaining").text
    assert "disabled>보고 대상" in html.replace('class="primary" disabled>', "disabled>")
    assert f"검토 #{rid}" in c.get("/?run=99").text
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404


def test_login_form_cookie_flow(tmp_path, monkeypatch):
    """브라우저는 기본 인증 창 대신 앱 로그인 화면 + 쿠키로 들어온다(Cloud Run 앞단 401 회피)."""
    m = _fresh(monkeypatch, tmp_path, PV_BASIC_USER="judge", PV_BASIC_PASS="s3cret")
    c = TestClient(m.app, follow_redirects=False)
    r = c.get("/remaining?run=1")
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    assert "www-authenticate" not in {k.lower() for k in r.headers}
    lp = c.get(r.headers["location"])
    assert lp.status_code == 200 and "비밀번호" in lp.text and 'name="next" value="/remaining?run=1"' in lp.text
    assert c.get("/api/run/1").status_code == 401 and c.get("/log.csv?run=1").status_code == 401
    assert c.get("/static/style.css").status_code == 200
    bad = c.post("/login", data={"user": "judge", "password": "x", "next": "/"})
    assert bad.status_code == 303 and "bad=1" in bad.headers["location"] and "pv_auth" not in bad.headers.get("set-cookie", "")
    ok = c.post("/login", data={"user": "judge", "password": "s3cret", "next": "/remaining?run=1"})
    assert ok.status_code == 303 and ok.headers["location"] == "/remaining?run=1"
    sc = ok.headers["set-cookie"]
    assert "pv_auth=" in sc and "httponly" in sc.lower() and "s3cret" not in sc
    assert "s3cret" not in ok.headers["location"]
    assert c.get("/").status_code == 200  # 쿠키로 통과
    # 다른 사이트로 돌려보내지 않는다
    for evil in ("//evil.example", "https://evil.example", "/\\evil.example"):
        r2 = c.post("/login", data={"user": "judge", "password": "s3cret", "next": evil})
        assert r2.headers["location"] == "/"
    # 쿠키 값은 비밀번호가 바뀌면 무효
    tok = m._auth_token()
    m2 = _fresh(monkeypatch, tmp_path, PV_BASIC_USER="judge", PV_BASIC_PASS="other")
    c2 = TestClient(m2.app, follow_redirects=False, cookies={"pv_auth": tok})
    assert c2.get("/").status_code == 303


def test_bad_input_shows_html_page_not_json(app_mod):
    c = TestClient(app_mod.app)
    for bad in ("아토르바스타틴", "123", "a"):
        r = c.post("/run", data={"drug1": bad})
        assert r.status_code == 400 and "text/html" in r.headers["content-type"]
        assert "영문 3~40자" in r.text and "돌아가기" in r.text and '"detail"' not in r.text
    r = c.post("/run", data={"drug1": " ", "drug2": "", "drug3": ""})
    assert r.status_code == 400 and "하나 이상" in r.text
    r = c.post("/decide", data={"run_id": 999, "drug": "a", "key": "k", "decision": "report"})
    assert r.status_code == 404 and "text/html" in r.headers["content-type"]
    r = c.post("/decide", data={"drug": "a"})
    assert r.status_code == 422 and "입력" in r.text


def test_ops_band_and_default_drugs(tmp_path, monkeypatch):
    m = _fresh(monkeypatch, tmp_path, PV_OPS_BAND="1", PV_DEFAULT_DRUGS="clozapine, Isotretinoin")
    html = TestClient(m.app).get("/").text
    assert "심사위원 직접 실행판" in html and 'value="clozapine"' in html and 'value="isotretinoin"' in html


def test_zero_new_papers_explained(app_mod):
    rid = app_mod.store.new_run(["atorvastatin"])
    app_mod.store.update_run(rid, status="done", stats={"found": 40, "already_seen": 40, "new": 0, "phase": "끝"})
    c = TestClient(app_mod.app)
    assert "모두 앞 검토에서 이미 본 것" in c.get(f"/?run={rid}").text
    assert "모두 앞 검토에서 본 것" in c.get(f"/remaining?run={rid}").text
