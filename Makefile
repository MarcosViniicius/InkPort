.PHONY: help install install-optional run dev test smoke convert crosspoint docker network clean

help:
	@echo "Comandos:"
	@echo "  make install           instala dependências"
	@echo "  make install-optional  instala extras (PyMuPDF)"
	@echo "  make run               inicia o servidor"
	@echo "  make dev               inicia com auto-reload"
	@echo "  make test              roda os testes"
	@echo "  make docker            build + up via docker compose"
	@echo "  make clean             remove caches e temporários"

install:
	python -m pip install -r requirements.txt

install-optional:
	python -m pip install -r requirements-optional.txt

run:
	python -m app

dev:
	uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload

test:
	python tests/smoke.py && python tests/conversions.py && python tests/rss_feeds.py && python tests/crosspoint_compat.py

smoke:
	python tests/smoke.py

convert:
	python tests/conversions.py

crosspoint:
	python tests/crosspoint_compat.py

docker:
	docker compose up -d --build

network:
	python -c "from app.config import get_settings; s=get_settings(); print('bind:', s.host, s.port); [print(' ', u) for u in s.access_urls]"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache data/temp/* 2>/dev/null || true
