FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# DEV=true adds the test tooling; production builds leave it out.
ARG DEV=false
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$DEV" = "true" ]; then pip install --no-cache-dir -r requirements-dev.txt; fi

COPY . .

CMD ["python", "main.py"]
