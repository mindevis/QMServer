FROM python:3.13-slim-bookworm

WORKDIR /app

# Install git
RUN apt-get update && apt-get install -y git

COPY requirements-prod.txt .

RUN pip install --no-cache-dir -r requirements-prod.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
