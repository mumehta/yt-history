.PHONY: help install parse enrich export all summary test clean clean-output

help: ## Show available targets and descriptions.
	@echo "Available targets:"
	@echo "  install       Install project dependencies with uv"
	@echo "  parse         Parse Takeout ZIP files into SQLite"
	@echo "  enrich        Fetch YouTube metadata with the YouTube Data API"
	@echo "  export        Export UTF-8 BOM CSV from SQLite"
	@echo "  all           Run parse, enrich, then export"
	@echo "  summary       Print a database summary"
	@echo "  test          Run pytest"
	@echo "  clean         Remove temporary files under temp/"
	@echo "  clean-output  Remove generated SQLite and CSV files under output/"

install: ## Install dependencies using uv.
	uv sync

parse: ## Parse Takeout ZIPs into SQLite.
	uv run python -u youtube_history_pipeline.py parse

enrich: ## Fetch YouTube metadata.
	uv run python youtube_history_pipeline.py enrich

export: ## Export CSV.
	uv run python youtube_history_pipeline.py export

all: ## Run parse, enrich, and export.
	uv run python youtube_history_pipeline.py all

summary: ## Print database summary.
	uv run python youtube_history_pipeline.py summary

test: ## Run pytest.
	uv run pytest

clean: ## Remove temp files while preserving temp/.gitkeep.
	uv run python -c "from pathlib import Path; import shutil; [shutil.rmtree(p) if p.is_dir() else p.unlink() for p in Path('temp').iterdir() if p.name != '.gitkeep']"

clean-output: ## Remove generated output files while preserving output/.gitkeep.
	uv run python -c "from pathlib import Path; [p.unlink() for p in Path('output').glob('*') if p.name != '.gitkeep' and p.suffix in {'.sqlite', '.csv'}]"
