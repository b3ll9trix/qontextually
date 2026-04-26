# ============================================================================
# Makefile for the Qontextually extraction pipeline
# ============================================================================
# Author: Reshma Suresh (https://github.com/b3ll9trix)
# ============================================================================

.PHONY: setup migrate run api api-stop agent agent-replay ui-install ui-dev ui-build ui-sync demo clean deep-clean all install-uv

# Define the sandbox environment
VENV = .venv
PYTHON = $(VENV)/bin/python

# Dynamically locate uv, or set the default path where the installer puts it
UV := $(shell command -v uv 2> /dev/null || echo $(HOME)/.local/bin/uv)

# The default command when someone just types 'make'
all: setup migrate run

# Rule to auto-install uv if it is completely missing from the system
install-uv:
	@if ! command -v uv >/dev/null 2>&1 && [ ! -f "$(UV)" ]; then \
		echo "🚀 'uv' not found. Auto-installing Astral uv (Lightning-fast package manager)..."; \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
	fi

# Create the venv exactly once. Do NOT depend on requirements.txt here —
# venv creation is orthogonal to dependency sync.
$(VENV)/pyvenv.cfg: | install-uv
	@echo "⚡ Building Python sandbox with uv..."
	$(UV) venv $(VENV)

# Re-sync dependencies whenever requirements.txt changes. The stamp file lets
# Make tell whether the last sync matches the current reqs, without touching
# venv-created files.
$(VENV)/.deps.stamp: requirements.txt | $(VENV)/pyvenv.cfg
	@echo "📦 Installing dependencies at lightspeed..."
	$(UV) pip install -p $(PYTHON) -r requirements.txt
	@touch $@

setup: $(VENV)/.deps.stamp
migrate: setup
	@echo "🗄️ Applying database migrations..."
	$(PYTHON) db/setup.py --db db/qontextually.db --migrations migrations

run: migrate
	@echo "🚀 Starting the Qontextually extraction pipeline..."
sqlite-shell: setup
	@echo "🐚 Launching interactive SQLite shell..."
	PYTHONPATH=. $(PYTHON) scripts/sqlite_shell.py

ui: setup
	@echo "🌐 Launching the Qontext Virtual File System (Database Browser)..."
	$(eval VEC_PATH := $(shell $(PYTHON) -c "import sqlite_vec, os; print(os.path.join(os.path.dirname(sqlite_vec.__file__), 'vec0' + ('.dylib' if os.uname().sysname == 'Darwin' else '.so')))"))
	$(VENV)/bin/sqlite_web -p 8081 -e $(VEC_PATH) db/qontextually.db

api: migrate
	@echo "🔌 Starting Qontextually API on http://127.0.0.1:8000 ..."
	@bash scripts/start_api.sh

api-stop:
	@echo "🛑 Stopping API server ..."
	@pkill -9 -f "uvicorn lib.api" 2>/dev/null || true
	@echo "  done"

# ----------------------------------------------------------------------------
# Web UI (vendored TanStack Start app, originally built in Lovable). Lives in
# ui/. Bun is preferred (matches bun.lockb); npm fallback also works.
# ----------------------------------------------------------------------------
NODE_PM := $(shell command -v bun >/dev/null 2>&1 && echo bun || echo npm)

ui/node_modules: ui/package.json
	@echo "📦 Installing UI deps with $(NODE_PM)..."
	@cd ui && $(NODE_PM) install

ui-install: ui/node_modules

ui-dev: ui-install
	@echo "🌐 Starting UI dev server (default http://localhost:5173)"
	@cd ui && $(NODE_PM) run dev

ui-build: ui-install
	@cd ui && $(NODE_PM) run build

# Re-snapshot the UI source from the upstream Lovable repo. Run this whenever
# you've iterated in Lovable and want the changes mirrored here.
#   make ui-sync                                   # default repo
#   make ui-sync UI_REPO=<other-git-url>           # override
# Preserves ui/README.md (our credit file) and ui/node_modules. Review the
# result with `git diff ui/` before committing.
UI_REPO ?= git@github.com:b3ll9trix/qontextual-navigator.git
UI_SYNC_TMP := /tmp/qontextually-ui-sync
ui-sync:
	@echo "📥 Cloning $(UI_REPO) ..."
	@rm -rf $(UI_SYNC_TMP)
	@git clone --depth 1 $(UI_REPO) $(UI_SYNC_TMP)
	@echo "🔄 Syncing into ui/ (preserving README.md, node_modules)..."
	@rsync -a --delete \
	    --exclude='node_modules' --exclude='dist' --exclude='.git' --exclude='README.md' \
	    $(UI_SYNC_TMP)/ ui/
	@rm -rf $(UI_SYNC_TMP)
	@echo "✅ UI synced. Review with: git diff ui/  (and rerun: make ui-install if package.json changed)"

# One-command demo: API detached on :8000, UI dev server in foreground.
# Stop the UI with Ctrl-C, then `make api-stop` to stop the backend.
demo: api ui-dev
clean:
	@echo "🧹 Sweeping up the sandbox and database..."
	rm -rf $(VENV)
	rm -f qontextually.db
	rm -rf __pycache__
	@echo "✨ Project environment is completely clean."

deep-clean: clean
	@echo "🧨 Removing Astral uv from the system..."
	rm -f $(UV)
	@echo "💥 All traces of uv have been removed."
