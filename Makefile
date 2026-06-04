# vpnpilot — developer Makefile.
#
# Targets:
#   make install-dev   create a venv at .venv and install in editable mode
#   make run           launch the tray app from the venv
#   make test          run pytest
#   make lint          run ruff
#   make rpm           build an installable .rpm into ./dist
#   make sdist         build the source tarball used by rpmbuild
#   make clean         remove build artifacts (keeps .venv)

PROJECT := vpnpilot
VERSION := $(shell awk -F'"' '/^version/ {print $$2; exit}' pyproject.toml)
SHELL := /bin/bash

VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
PYTEST  := $(VENV)/bin/pytest
RUFF    := $(VENV)/bin/ruff

DIST    := dist
RPMTOP  := $(CURDIR)/.rpmbuild
RPMTMP  := $(RPMTOP)/tmp

.PHONY: help install-dev run test lint rpm sdist clean

help:
	@echo "Targets: install-dev | run | test | lint | rpm | sdist | clean"

$(VENV)/bin/activate:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip wheel

install-dev: $(VENV)/bin/activate
	$(PIP) install -e ".[dev]"

run: install-dev
	$(PY) -m vpnpilot

test: install-dev
	$(PYTEST) -q

lint: install-dev
	$(RUFF) check src tests
	$(RUFF) format --check src tests

sdist:
	@mkdir -p $(DIST)
	@rm -f $(DIST)/$(PROJECT)-$(VERSION).tar.gz
	git ls-files -z | tar --null --files-from - \
		--transform='s#^#$(PROJECT)-$(VERSION)/#' \
		-czf $(DIST)/$(PROJECT)-$(VERSION).tar.gz

rpm: sdist
	@command -v rpmbuild >/dev/null 2>&1 || { \
		echo "rpmbuild not found. Install with: sudo dnf install rpm-build python3-devel pyproject-rpm-macros desktop-file-utils"; exit 1; }
	rm -rf $(RPMTOP)/BUILD $(RPMTOP)/BUILDROOT $(RPMTMP)
	mkdir -p $(RPMTOP)/{SOURCES,SPECS,BUILD,BUILDROOT,RPMS,SRPMS,tmp}
	cp $(DIST)/$(PROJECT)-$(VERSION).tar.gz $(RPMTOP)/SOURCES/
	cp packaging/$(PROJECT).spec $(RPMTOP)/SPECS/
	rpmbuild --define "_topdir $(RPMTOP)" --define "_tmppath $(RPMTMP)" --define "_smp_build_ncpus 1" -bb $(RPMTOP)/SPECS/$(PROJECT).spec
	cp $(RPMTOP)/RPMS/noarch/$(PROJECT)-$(VERSION)-*.noarch.rpm $(DIST)/
	@echo
	@echo "Built RPM(s) in $(DIST):"
	@ls -1 $(DIST)/*.rpm

clean:
	rm -rf $(DIST) $(RPMTOP) build src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
