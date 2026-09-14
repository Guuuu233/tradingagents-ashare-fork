from pathlib import Path, PurePath
from _pytest.main import fnmatch_ex

try:
    import tomllib
except ImportError:
    import tomli as tomllib


def test_pytest_discovery_scope_contracts(pytestconfig):
    """Regression test for DAV-928: pytest discovery scope and norecursedirs contracts."""
    # 1. Verify runtime pytest configuration contracts
    testpaths = pytestconfig.getini("testpaths")
    assert testpaths == ["tests"], f"Expected testpaths to be ['tests'], got {testpaths}"

    norecursedirs = pytestconfig.getini("norecursedirs")
    required_patterns = {
        ".*",
        "build",
        "dist",
        "*.egg",
        "venv",
        "node_modules",
        "frontend/node_modules",
        "work",
        "data",
        "eval_results",
        "reports",
        "results",
    }
    assert required_patterns.issubset(set(norecursedirs)), (
        f"Missing required norecursedirs patterns: {required_patterns - set(norecursedirs)}"
    )

    # 2. Verify candidate directories to exclude are matched by norecursedirs
    candidate_paths = [
        "work",
        "data",
        "node_modules",
        "frontend/node_modules",
        "eval_results",
        "reports",
        "results",
    ]
    for path_str in candidate_paths:
        assert any(fnmatch_ex(pat, PurePath(path_str)) for pat in norecursedirs), (
            f"Expected {path_str} to be matched by norecursedirs patterns"
        )

    # 3. Verify tests directory itself is not matched
    assert not any(fnmatch_ex(pat, PurePath("tests")) for pat in norecursedirs), (
        "tests directory must not be matched by norecursedirs"
    )

    # 4. Verify pyproject.toml static configuration
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    ini_opts = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    assert ini_opts.get("testpaths") == ["tests"]
    assert "work" in ini_opts.get("norecursedirs", [])
    assert "data" in ini_opts.get("norecursedirs", [])
