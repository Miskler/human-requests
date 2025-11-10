# === Paths ===
VENV        ?= .venv
PY          := $(VENV)/bin/python
PIP         := $(VENV)/bin/pip
UVICORN     := $(VENV)/bin/uvicorn
PYTEST      := $(VENV)/bin/pytest

SERVER_DIR  ?= .
APP         ?= app:app           # модуль:объект ASGI внутри SERVER_DIR
HOST        ?= 127.0.0.1
PORT        ?= 8980
PID_FILE    ?= .server.pid

# === Helpers ===
.PHONY: help
help: ## Показать цели
	@echo "Usage: make <target>"
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' Makefile | sort | sed 's/:.*##/: /'

$(VENV)/bin/activate: ## Создать venv
	python3 -m venv $(VENV)
	@echo "venv created at $(VENV)"

.PHONY: install
install: $(VENV)/bin/activate ## Установить зависимости сервера + pytest
	$(PIP) install -U pip
	$(PIP) install -e $(SERVER_DIR)
	$(PIP) install pytest

# === Server run (foreground / background) ===
.PHONY: serve
serve: install ## Запуск uvicorn в форграунде (из подпапки)
	$(UVICORN) $(APP) --host $(HOST) --port $(PORT) --reload --app-dir $(SERVER_DIR)

.PHONY: serve-bg
serve-bg: install ## Запуск uvicorn в фоне с PID-файлом
	@if [ -f "$(PID_FILE)" ] && kill -0 $$(cat "$(PID_FILE)") 2>/dev/null; then \
		echo "Already running (PID $$(cat $(PID_FILE)))"; exit 0; \
	fi
	@echo "Starting server at http://$(HOST):$(PORT)"
	@nohup $(UVICORN) $(APP) --host $(HOST) --port $(PORT) --reload --app-dir $(SERVER_DIR) >/dev/null 2>&1 & echo $$! > $(PID_FILE)
	@sleep 0.5
	@echo "PID: $$(cat $(PID_FILE))"

.PHONY: stop
stop: ## Остановить сервер, запущенный через serve-bg
	@if [ -f "$(PID_FILE)" ]; then \
		PID=$$(cat "$(PID_FILE)"); \
		if kill -0 $$PID 2>/dev/null; then \
			echo "Stopping PID $$PID"; \
			kill $$PID; \
		else \
			echo "Stale PID file"; \
		fi; \
		rm -f "$(PID_FILE)"; \
	else \
		echo "No PID file"; \
	fi

.PHONY: restart
restart: stop serve-bg ## Перезапуск фонового сервера

# === Tests ===
.PHONY: test
test: install ## pytest из подпапки сервера
	cd $(SERVER_DIR) && $(PYTEST)

# === Smoke ===
CURL := curl -sS -D - -o /dev/null

.PHONY: smoke
smoke: ## Быстрый опрос эндпоинтов (нужен запущенный сервер)
	@echo "GET /base"
	@$(CURL) http://$(HOST):$(PORT)/base | head -n 1
	@echo "GET /api/base"
	@$(CURL) http://$(HOST):$(PORT)/api/base | head -n 1
	@echo "GET /api/challenge"
	@$(CURL) http://$(HOST):$(PORT)/api/challenge | head -n 1

# === Clean ===
.PHONY: clean
clean: ## Удалить кэш питона/pytest-артефакты
	@find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	@rm -rf .pytest_cache .mypy_cache .ruff_cache
