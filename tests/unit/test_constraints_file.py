import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _constraints() -> dict[str, str]:
    pins = {}
    for line in (ROOT / "constraints.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            name, _, version = line.partition("==")
            pins[_normalise(name)] = version
    return pins


def test_every_runtime_dependency_is_pinned_to_an_exact_version():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    pins = _constraints()
    names = [re.split(r"[\[<>=!~ ;]", dep, 1)[0] for dep in project["dependencies"]]
    missing = sorted(_normalise(n) for n in names if _normalise(n) not in pins)
    assert not missing, f"not pinned in constraints.txt: {missing}"


def test_constraints_contain_only_exact_pins():
    for line in (ROOT / "constraints.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            assert re.fullmatch(r"[A-Za-z0-9_.\-]+==[0-9][^\s=]*", line), line
