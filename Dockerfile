FROM python:3.12.10-slim-bookworm

WORKDIR /srv/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY main.py ./main.py
# The SDAIA evidence index the Policy Compliance Agent reads at runtime.
# Copied last: it changes far less often than the source above.
COPY data/vectorstore/sdaia_faiss ./data/vectorstore/sdaia_faiss

RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /srv/app
USER appuser

EXPOSE 8000
CMD ["python", "main.py"]
