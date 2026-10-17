"""Vercel installs from pyproject.toml, local development from requirements.txt. They must list the same packages."""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _norm(spec: str) -> str:
    return re.sub(r"\s+", "", spec).lower()


def test_pyproject_dependencies_match_requirements_txt():
    declared = {_norm(d) for d in tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]}
    wanted = {_norm(line) for line in (ROOT / "requirements.txt").read_text().splitlines() if line.strip() and not line.startswith(("#", "-"))}
    assert declared == wanted, f"only in pyproject: {declared - wanted}; only in requirements.txt: {wanted - declared}"


def test_every_third_party_import_is_a_declared_dependency():
    declared = " ".join(tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]).lower()
    mapping = {"fastapi": "fastapi", "httpx": "httpx", "dotenv": "python-dotenv", "svix": "svix", "psycopg": "psycopg", "pydantic": "fastapi"}
    imported = set()
    for f in list((ROOT / "mailcore").glob("*.py")) + [ROOT / "main.py"]:
        imported |= set(re.findall(r"^(?:from|import)\s+([a-z_]+)", f.read_text(), re.M))
    missing = [m for m in imported if m in mapping and mapping[m] not in declared]
    assert not missing, missing
