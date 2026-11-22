"""라이너 API 호출과 호출 기록(횟수·비용).

키는 환경 변수 LINER_API_KEY 에서만 읽는다. 키 값은 로그·예외 메시지·파일 어디에도 쓰지 않는다.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field

import httpx

BASE = os.environ.get("LINER_API_BASE", "https://platform.liner.com/api/v1")
MARK_MODEL = "liner-mark-1.3"

# 라이너 공개 가격표(2026-11 기준). 1,000번당 달러, 모델은 100만 토큰당 달러.
PRICE_SCHOLAR = 0.3 / 1000
PRICE_WEB = 1.0 / 1000  # include_knowledge=false
PRICE_MARK_IN = 1.0 / 1_000_000
PRICE_MARK_OUT = 5.0 / 1_000_000


class LinerError(RuntimeError):
    pass


class MissingKey(LinerError):
    pass


def _key() -> str:
    k = os.environ.get("LINER_API_KEY", "").strip()
    if not k:
        raise MissingKey("LINER_API_KEY 환경 변수가 없습니다.")
    return k


@dataclass
class Ledger:
    """한 번 돌릴 때의 호출 수와 비용."""

    scholar_calls: int = 0
    web_calls: int = 0
    mark_calls: int = 0
    mark_in_tokens: int = 0
    mark_out_tokens: int = 0
    errors: int = 0
    seconds: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def cost_usd(self) -> float:
        return (
            self.scholar_calls * PRICE_SCHOLAR
            + self.web_calls * PRICE_WEB
            + self.mark_in_tokens * PRICE_MARK_IN
            + self.mark_out_tokens * PRICE_MARK_OUT
        )

    def as_dict(self) -> dict:
        return {
            "scholar_calls": self.scholar_calls,
            "web_calls": self.web_calls,
            "mark_calls": self.mark_calls,
            "mark_in_tokens": self.mark_in_tokens,
            "mark_out_tokens": self.mark_out_tokens,
            "errors": self.errors,
            "api_seconds": round(self.seconds, 1),
            "cost_usd": round(self.cost_usd, 5),
        }


class LinerClient:
    def __init__(self, ledger: Ledger | None = None, timeout: float = 60.0, transport=None):
        self.ledger = ledger or Ledger()
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self._scholar_lock = threading.Lock()
        self._last_scholar = 0.0

    # --- 내부 ---
    def _post(self, path: str, body: dict, auth: str) -> dict:
        headers = {"Content-Type": "application/json"}
        if auth == "bearer":
            headers["Authorization"] = "Bearer " + _key()
        else:
            headers["x-api-key"] = _key()
        last_err = ""
        for attempt in range(3):
            t0 = time.time()
            try:
                r = self._client.post(BASE + path, json=body, headers=headers)
            except httpx.HTTPError as e:
                last_err = type(e).__name__
                time.sleep(1.5 * (attempt + 1))
                continue
            finally:
                with self.ledger._lock:
                    self.ledger.seconds += time.time() - t0
            if r.status_code == 429 or r.status_code >= 500:
                last_err = f"HTTP {r.status_code}"
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code != 200:
                # 응답 본문은 남기지 않는다(키 조각이 되돌아올 가능성까지 막음). 상태 코드와 에러 종류만.
                code = ""
                try:
                    err = r.json().get("error")
                    code = (err.get("code") or err.get("type")) if isinstance(err, dict) else ""
                except Exception:
                    pass
                raise LinerError(f"HTTP {r.status_code} {str(code)[:40]}".strip())
            try:
                return r.json()
            except ValueError:
                raise LinerError("응답이 JSON이 아님")
        with self.ledger._lock:
            self.ledger.errors += 1
        raise LinerError(f"라이너 호출 실패: {last_err}")

    # --- 공개 ---
    def scholar(self, query: str, max_results: int = 20, date_range: str | None = "past_year") -> list[dict]:
        # 학술 검색은 초당 2번 제한이 있다.
        with self._scholar_lock:
            wait = 0.55 - (time.time() - self._last_scholar)
            if wait > 0:
                time.sleep(wait)
            self._last_scholar = time.time()
        body = {"query": query, "max_results": max_results}
        if date_range:
            body["date_range"] = date_range
        data = self._post("/tools/search/scholar", body, auth="x-api-key")
        with self.ledger._lock:
            self.ledger.scholar_calls += 1
        return data.get("results") or []

    def mark_json(self, system: str, user: str, schema: dict, max_tokens: int = 900) -> dict:
        body = {
            "model": MARK_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": schema,
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        data = self._post("/chat/completions", body, auth="bearer")
        usage = data.get("usage") or {}
        with self.ledger._lock:
            self.ledger.mark_calls += 1
            self.ledger.mark_in_tokens += int(usage.get("prompt_tokens") or 0)
            self.ledger.mark_out_tokens += int(usage.get("completion_tokens") or 0)
        try:
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            raise LinerError(f"모델 응답을 읽지 못함: {type(e).__name__}")

    def balance_usd(self) -> float | None:
        """잔액 조회(무료)."""
        try:
            r = self._client.get(BASE + "/credits/balance", headers={"x-api-key": _key()})
            return float(r.json()["balance_usd"])
        except Exception:
            return None
