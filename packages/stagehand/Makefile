.PHONY: setup test example

setup:           ## sync the env (incl. dev deps) from pyproject
	uv sync

test:            ## run the unit tests
	uv run python -m pytest -q

example:         ## run the worked staircase (fake compute), writes runs/status.html
	uv run python examples/sweep.py
