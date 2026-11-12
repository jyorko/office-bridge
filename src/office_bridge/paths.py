"""Paths to immutable native assets shipped with the action implementation."""

from pathlib import Path

NATIVE = Path(__file__).resolve().parent / "native"
TEAMS_NATIVE = NATIVE / "teams"
