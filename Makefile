# ============================================================================
# Makefile for the Qontextually extraction pipeline
# ============================================================================
# Author: Reshma Suresh (https://github.com/b3ll9trix)
# ============================================================================

.PHONY: setup migrate run clean deep-clean all install-uv

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

# Rule to create the sandbox and install requirements ONLY if they changed
$(VENV)/bin/activate: requirements.txt | install-uv
	@echo "⚡ Building Python sandbox with uv..."
	$(UV) venv $(VENV)
	@echo "📦 Installing dependencies at lightspeed..."
	$(UV) pip install -p $(PYTHON) -r requirements.txt
	@touch $(VENV)/bin/activate

setup: $(VENV)/bin/activate
migrate: setup
	@echo "🗄️ Applying database migrations..."
	$(PYTHON) db/setup.py --db db/qontextually.db --migrations migrations

run: migrate
	@echo "🚀 Starting the Qontextually extraction pipeline..."

clean:
	@echo "🧹 Sweeping up the sandbox and database..."
	rm -rf $(VENV)
	rm -f qontext.db
	rm -rf __pycache__
	@echo "✨ Project environment is completely clean."

deep-clean: clean
	@echo "🧨 Removing Astral uv from the system..."
	rm -f $(UV)
	@echo "💥 All traces of uv have been removed."
