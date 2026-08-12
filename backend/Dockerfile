FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN python -m pip install --upgrade pip && python -m pip install -e ".[dev]"

EXPOSE 8000

CMD ["uvicorn", "two_stage.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
