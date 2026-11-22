"""SQLite 저장: 본 논문 목록, 돌린 기록, 판정, 사람이 누른 결정."""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import threading

DEFAULT_DB = os.environ.get(
    "PV_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "pv.sqlite3")
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
  drug TEXT NOT NULL, key TEXT NOT NULL, first_run INTEGER, first_seen TEXT,
  PRIMARY KEY (drug, key)
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, finished TEXT,
  drugs TEXT, status TEXT, stats TEXT, ledger TEXT, log TEXT
);
CREATE TABLE IF NOT EXISTS papers (
  run_id INTEGER NOT NULL, key TEXT NOT NULL, drug TEXT NOT NULL,
  title TEXT, abstract TEXT, url TEXT, date TEXT, authors TEXT, journal TEXT, queries TEXT,
  verdict TEXT, judgment TEXT,
  decision TEXT, decided_at TEXT, note TEXT,
  PRIMARY KEY (run_id, drug, key)
);
CREATE TABLE IF NOT EXISTS decision_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, run_id INTEGER, drug TEXT, key TEXT,
  decision TEXT, note TEXT, reviewer TEXT
);
"""

DECISIONS = {"report": "보고 대상", "not_report": "보고 대상 아님", "later": "나중에"}


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str = DEFAULT_DB):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        # 서버가 꺼지며 「도는 중」으로 남은 회차를 정리한다.
        self.db.execute("UPDATE runs SET status='failed' WHERE status='running'")
        self.db.commit()

    # --- 본 목록 ---
    def is_seen(self, drug: str, key: str) -> bool:
        r = self.db.execute("SELECT 1 FROM seen WHERE drug=? AND key=?", (drug, key)).fetchone()
        return r is not None

    def mark_seen(self, drug: str, key: str, run_id: int | None):
        with self._lock:
            self.db.execute(
                "INSERT OR IGNORE INTO seen(drug,key,first_run,first_seen) VALUES (?,?,?,?)",
                (drug, key, run_id, now()),
            )
            self.db.commit()

    def seen_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM seen").fetchone()[0]

    # --- 돌린 기록 ---
    def new_run(self, drugs: list[str]) -> int:
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO runs(started,drugs,status,stats,ledger,log) VALUES (?,?,?,?,?,?)",
                (now(), json.dumps(drugs), "running", "{}", "{}", "[]"),
            )
            self.db.commit()
            return cur.lastrowid

    def update_run(self, run_id: int, **kw):
        sets, vals = [], []
        for k, v in kw.items():
            sets.append(f"{k}=?")
            vals.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
        with self._lock:
            self.db.execute(f"UPDATE runs SET {','.join(sets)} WHERE id=?", (*vals, run_id))
            self.db.commit()

    def get_run(self, run_id: int) -> dict | None:
        r = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return _run(r) if r else None

    def latest_run(self) -> dict | None:
        r = self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return _run(r) if r else None

    def runs(self) -> list[dict]:
        return [_run(r) for r in self.db.execute("SELECT * FROM runs ORDER BY id DESC").fetchall()]

    def runs_today(self) -> int:
        day = dt.date.today().isoformat()
        return self.db.execute("SELECT COUNT(*) FROM runs WHERE started LIKE ?", (day + "%",)).fetchone()[0]

    # --- 논문 ---
    def save_paper(self, run_id: int, p: dict, verdict: str, judgment: dict):
        with self._lock:
            self.db.execute(
                "INSERT OR REPLACE INTO papers(run_id,key,drug,title,abstract,url,date,authors,journal,queries,verdict,judgment)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id, p["key"], p["drug"], p["title"], p["abstract"], p["url"],
                    json.dumps(p["date"], ensure_ascii=False), json.dumps(p["authors"], ensure_ascii=False),
                    p["journal"], json.dumps(p["queries"], ensure_ascii=False), verdict,
                    json.dumps(judgment, ensure_ascii=False),
                ),
            )
            self.db.commit()

    def papers(self, run_id: int, verdicts: tuple[str, ...] | None = None) -> list[dict]:
        rows = self.db.execute("SELECT * FROM papers WHERE run_id=? ORDER BY drug, title", (run_id,)).fetchall()
        out = [_paper(r) for r in rows]
        if verdicts:
            out = [p for p in out if p["verdict"] in verdicts]
        return out

    def get_paper(self, run_id: int, drug: str, key: str) -> dict | None:
        r = self.db.execute(
            "SELECT * FROM papers WHERE run_id=? AND drug=? AND key=?", (run_id, drug, key)
        ).fetchone()
        return _paper(r) if r else None

    def open_cards(self, verdicts: tuple[str, ...]) -> list[dict]:
        """모든 회차에서 아직 끝나지 않은 카드: 남길 판정이면서 결정이 없거나 「나중에」인 것.
        같은 논문이 여러 회차에 있으면(판정 실패 뒤 다시 본 경우) 가장 최근 것만."""
        q = ",".join("?" * len(verdicts))
        rows = self.db.execute(
            f"SELECT p.*, r.started AS run_started FROM papers p JOIN runs r ON r.id=p.run_id "
            f"WHERE p.verdict IN ({q}) AND (p.decision IS NULL OR p.decision='later') ORDER BY p.run_id DESC",
            verdicts,
        ).fetchall()
        out, seen = [], set()
        for r in rows:
            d = _paper(r)
            k = (d["drug"], d["key"])
            if k in seen:
                continue
            seen.add(k)
            out.append(d)
        return out

    def decision_history(self, run_id: int, drug: str, key: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM decision_log WHERE run_id=? AND drug=? AND key=? ORDER BY id", (run_id, drug, key))]

    def decide(self, run_id: int, drug: str, key: str, decision: str, note: str = "", reviewer: str = ""):
        if decision not in DECISIONS:
            raise ValueError("알 수 없는 결정")
        with self._lock:
            self.db.execute(
                "INSERT INTO decision_log(at,run_id,drug,key,decision,note,reviewer) VALUES (?,?,?,?,?,?,?)",
                (now(), run_id, drug, key, decision, note[:500], reviewer[:80]),
            )
            self.db.execute(
                "UPDATE papers SET decision=?, decided_at=?, note=? WHERE run_id=? AND drug=? AND key=?",
                (decision, now(), note[:500], run_id, drug, key),
            )
            self.db.commit()


def _run(r) -> dict:
    d = dict(r)
    for k in ("drugs", "stats", "ledger", "log"):
        d[k] = json.loads(d[k] or ("[]" if k in ("drugs", "log") else "{}"))
    return d


def _paper(r) -> dict:
    d = dict(r)
    for k in ("date", "authors", "queries", "judgment"):
        d[k] = json.loads(d[k]) if d[k] else None
    return d
