FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY data ./data
RUN pip install --no-cache-dir -e .

ENV CEAP_ENV=prod \
    CEAP_AUTO_APPROVE=false \
    CEAP_LLM_PROVIDER=auto

EXPOSE 8000
CMD ["uvicorn", "ceap.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
