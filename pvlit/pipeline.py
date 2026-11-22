"""한 주 문헌 검토를 처음부터 끝까지 돌린다.

학술 검색(성분 × 질의) → 중복 빼기 → 지난번까지 본 논문 빼기 → 성분명 없는 결과 빼기(코드)
→ liner-mark 한 편씩 판정(동시 5개) → 코드로 원문 대조 → 저장

놓치지 않기 위한 규칙
- 검색이 하나라도 실패하면 회차를 「일부 실패」로 끝내고 화면에 빨간 띠를 띄운다(조용히 0편이 되지 않게).
- 키가 없으면 바로 「실패」.
- 판정 실패와 성분명 없음은 「본 목록」에 넣지 않는다. 다음 주에 다시 본다.
- 판정 상한(max_judge)을 넘는 논문은 판정하지 않고 「본 목록」에도 넣지 않는다(다음 회차에서 읽음).
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .judge import judge_paper, verdict
from .liner import LinerClient, Ledger, MissingKey
from .search import DEFAULT_DRUGS, collect
from .store import Store, now

KEEP = ("candidate", "check", "error")


def run_weekly(store: Store, drugs: dict[str, list[str]] | None = None, client: LinerClient | None = None,
               workers: int = 5, run_id: int | None = None, max_judge: int | None = None) -> int:
    drugs = drugs or DEFAULT_DRUGS
    client = client or LinerClient(Ledger())
    ledger = client.ledger
    run_id = run_id or store.new_run(list(drugs))
    log: list[str] = []
    stats = {"searches": 0, "search_failures": 0, "found": 0, "already_seen": 0, "no_drug_name": 0, "new": 0,
             "deferred": 0, "judged": 0, "candidates": 0, "check": 0, "not_case": 0, "errors": 0, "phase": "논문 찾는 중"}
    t_start = time.time()

    def push(msg: str):
        log.append(f"{now()[11:]} {msg}")
        stats["elapsed_sec"] = round(time.time() - t_start, 1)
        store.update_run(run_id, stats=stats, ledger=ledger.as_dict(), log=log[-200:])

    try:
        todo = []
        failures: list[str] = []
        for drug, names in drugs.items():
            found = collect(client, drug, names, progress=push, failures=failures)
            stats["searches"] = ledger.scholar_calls
            stats["search_failures"] = len(failures)
            stats["found"] += len(found)
            for p in found:
                if store.is_seen(drug, p["key"]):
                    stats["already_seen"] += 1
                    continue
                if not p["mentions_drug"]:
                    # 제목·초록에 성분명이 없으면 모델을 부르지 않는다. 기록에는 남기고 본 목록에는 넣지 않는다.
                    stats["no_drug_name"] += 1
                    store.save_paper(run_id, p, "no_drug_name", {"reason": "제목과 초록에 성분명이 없어 AI에게 보내지 않고 코드가 뺐습니다."})
                    continue
                todo.append((drug, names, p))
            push(f"{drug}: 찾은 논문 {len(found)}편")
        if max_judge is not None and len(todo) > max_judge:
            stats["deferred"] = len(todo) - max_judge
            todo = todo[:max_judge]
            push(f"한 번에 읽는 상한 {max_judge}편을 넘은 {stats['deferred']}편은 다음 검토로 미룸")
        stats["new"] = len(todo)
        stats["phase"] = "AI가 읽는 중"
        push(f"새 논문 {len(todo)}편을 AI가 한 편씩 읽기 시작(동시 {workers}편)" if todo else "새로 읽을 논문이 없어 AI 판정 없이 끝냄")

        def work(item):
            drug, names, p = item
            return drug, p, judge_paper(client, p, drug, names)

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(work, it) for it in todo]
            for f in as_completed(futs):
                drug, p, j = f.result()
                v = verdict(j)
                store.save_paper(run_id, p, v, j)
                if v != "error":
                    store.mark_seen(drug, p["key"], run_id)
                stats["judged"] += 1
                stats[{"candidate": "candidates", "check": "check", "not_case": "not_case"}.get(v, "errors")] += 1
                if stats["judged"] % 5 == 0 or stats["judged"] == len(todo):
                    push(f"AI가 읽은 논문 {stats['judged']}/{len(todo)}")
        if failures or stats["errors"]:
            stats["phase"] = "일부 실패"
            push(f"끝남(검색 실패 {len(failures)}번, 판정 실패 {stats['errors']}편, 빠진 논문이 있을 수 있음)")
            store.update_run(run_id, status="partial", finished=now())
        else:
            stats["phase"] = "끝남"
            push("끝남")
            store.update_run(run_id, status="done", finished=now())
    except MissingKey:
        stats["phase"] = "실패"
        push("실패: 라이너 키(LINER_API_KEY)가 설정되지 않았습니다.")
        store.update_run(run_id, status="failed", finished=now())
    except Exception as e:  # 화면에 실패를 보여 준다(키 값은 메시지에 없다)
        stats["phase"] = "실패"
        push(f"실패: {type(e).__name__}: {str(e)[:200]}")
        store.update_run(run_id, status="failed", finished=now())
    return run_id
