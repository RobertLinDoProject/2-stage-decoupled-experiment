.PHONY: setup dev stop backend-typecheck frontend-typecheck clean

setup:
	cd backend && python -m pip install -e ".[dev]"
	cd frontend && pnpm install

dev:
	docker compose up --build

stop:
	docker compose down

backend-typecheck:
	cd backend && python -m mypy src

frontend-typecheck:
	cd frontend && pnpm typecheck

clean:
	@echo "This repository now keeps only Data, M0-M9 core code, and decoupled two-stage experiment outputs."
	@echo "Generated decoupled two-stage runs are stored under storage/published/runs/."
