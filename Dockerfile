# 다진 백엔드 이미지
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
RUN pip install .

# 일반 사용자로 실행 (컨테이너가 뚫려도 피해를 줄인다)
RUN useradd --create-home app \
    && mkdir -p /data/uploads /srv/.cache \
    && chown -R app:app /data /srv
USER app

EXPOSE 8000
# 요청 횟수 제한이 서버 메모리 기반이라 워커는 1개로 둔다 (--workers 를 늘리면 제한이 어긋난다)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
