# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

# Nenhuma ferramenta externa e' necessaria para converter: tudo roda em Python
# e a unrar.dll (do Windows) vem dentro do projeto. O unico pacote do sistema e'
# o 'bsdtar' (libarchive-tools), que da' suporte a CBR/RAR no Linux via rarfile;
# sem ele o servidor continua funcionando, so' sem RAR.
# Uma camada so': instalar e limpar juntos (apagar depois NAO reduz a imagem).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libarchive-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencias primeiro: mudar o codigo nao invalida a camada do pip.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Só o que roda. Testes, docs e ferramentas de desenvolvimento ficam no repo.
COPY app ./app

RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8080

# Healthcheck com o proprio Python: dispensa instalar o curl (era a maior
# camada extra da imagem, ~13 MB). Le o PORT do ambiente para acompanhar.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os,urllib.request;port=os.environ.get('PORT','8080');urllib.request.urlopen('http://127.0.0.1:'+port+'/health', timeout=4)"

# Mesmo entrypoint documentado no README: respeita HOST, PORT e TRUST_PROXY.
CMD ["python", "-m", "app"]
