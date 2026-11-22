# 약물감시 주간 문헌 검토 — Cloud Run 용 이미지
# 키는 이미지에 넣지 않는다. 실행할 때 환경 변수 LINER_API_KEY(Secret Manager)로만 준다.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PV_DB=/tmp/pv.sqlite3 PV_SEED_DB=/srv/seed/pv_demo.sqlite3
WORKDIR /srv
COPY requirements-run.txt .
RUN pip install --no-cache-dir -r requirements-run.txt
COPY app ./app
COPY pvlit ./pvlit
COPY seed ./seed
# 코드·견본 DB 는 루트 소유(앱 사용자가 못 고침). 쓰는 곳은 /tmp 뿐이다.
RUN useradd -r -u 10001 app
USER app
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers"]
