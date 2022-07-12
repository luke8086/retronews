SCRIPTS = retronews.py

.PHONY: venv-check
venv-check:
ifeq ("$(VIRTUAL_ENV)","")
	@echo "Venv is not active, run make venv-build and \`make venv-activate\`"
	@echo
	exit 1
endif

venv-build:
	python3 -m venv .venv

venv-activate:
	@echo "source .venv/bin/activate"

install: venv-check
	pip install black isort flake8 mypy

lint: venv-check
	black $(SCRIPTS)
	isort $(SCRIPTS)
	flake8 $(SCRIPTS)
	mypy $(SCRIPTS)
