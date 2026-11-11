# Imagen base ligera de Python 3.11
FROM python:3.11-slim

# Evita archivos .pyc y fuerza logs sin buffer
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Instalar dependencias primero para aprovechar la caché de capas
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copiar el código de la aplicación
COPY . .

# Ejecutar como usuario sin privilegios
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

CMD ["python", "start.py"]
