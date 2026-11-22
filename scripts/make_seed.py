"""견본 DB 만들기: data/pv.sqlite3(11/19 실제 한 주 결과)에서 검토 #1·#2만 seed/pv_demo.sqlite3 로 복사한다.

라이너를 부르지 않는다. 초록은 라이너 학술 검색이 준 공개 논문 초록이고, 개인정보는 없다.
실행: .venv/bin/python scripts/make_seed.py [원본 DB] [출력]
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data", "pv.sqlite3")
out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "seed", "pv_demo.sqlite3")
KEEP_RUNS = (1, 2)

if os.path.exists(out):
    os.remove(out)
os.makedirs(os.path.dirname(out), exist_ok=True)
con = sqlite3.connect(src)
con.execute("VACUUM INTO ?", (out,))
con.close()

db = sqlite3.connect(out)
q = ",".join("?" * len(KEEP_RUNS))
db.execute(f"DELETE FROM runs WHERE id NOT IN ({q})", KEEP_RUNS)
db.execute(f"DELETE FROM papers WHERE run_id NOT IN ({q})", KEEP_RUNS)
db.execute(f"DELETE FROM decision_log WHERE run_id NOT IN ({q})", KEEP_RUNS)
db.execute(f"DELETE FROM seen WHERE first_run NOT IN ({q})", KEEP_RUNS)
db.commit()
db.execute("VACUUM")
db.close()

db = sqlite3.connect(out)
for t in ("runs", "papers", "seen", "decision_log"):
    print(t, db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
print("decided:", db.execute("SELECT COUNT(*) FROM papers WHERE decision IS NOT NULL").fetchone()[0])
print("->", out, os.path.getsize(out), "bytes")
