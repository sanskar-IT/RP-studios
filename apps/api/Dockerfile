FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml ./
COPY apps ./apps
COPY services ./services
COPY packages ./packages
COPY alembic ./alembic
COPY alembic.ini ./
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["uvicorn", "apps.api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
