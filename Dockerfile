FROM python:3.11-slim

WORKDIR /app

# Install deps first so this layer caches unless requirements change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml .
COPY src ./src
COPY examples ./examples
RUN pip install --no-cache-dir -e .

EXPOSE 8000

# Runs the HTTP surface. The audit trail and runs live in memory, so this is
# a single-process demo server, not a horizontally scaled service.
CMD ["uvicorn", "strands.api:app", "--host", "0.0.0.0", "--port", "8000"]
