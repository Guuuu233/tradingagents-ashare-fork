"""Tests for api/main.py social_data_context wiring (Task 9 / D-009 / §8).

Asserts that all three create_initial_state call sites in api/main.py
(dual-horizon, streaming, and single-horizon) properly forward social_data_context.
"""

import ast
from pathlib import Path


def test_api_main_three_create_initial_state_forward_social_data_context():
    """All three create_initial_state calls in api/main.py must pass social_data_context."""
    source_path = Path("api/main.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_initial_state"
    ]

    # Exactly three call sites: dual-horizon, streaming, single-horizon
    assert len(calls) == 3, f"Expected 3 create_initial_state calls in api/main.py, found {len(calls)}"

    for idx, call in enumerate(calls, 1):
        forwarded = {
            kw.arg: ast.unparse(kw.value)
            for kw in call.keywords
            if kw.arg
        }

        assert "social_data_context" in forwarded, (
            f"Call site #{idx} (line {getattr(call, 'lineno', '?')}) is missing keyword 'social_data_context'"
        )
        assert forwarded["social_data_context"] == "social_data_context", (
            f"Call site #{idx} forwards {forwarded['social_data_context']} instead of 'social_data_context'"
        )

        assert "market_data_context" in forwarded, (
            f"Call site #{idx} is missing keyword 'market_data_context'"
        )
        assert forwarded["market_data_context"] == "market_data_context"

        assert "runtime_config" in forwarded, (
            f"Call site #{idx} is missing keyword 'runtime_config'"
        )


def test_api_main_source_extracts_social_data_context_from_collected_pool():
    """Verify source code extracts social_data_context from collected_pool before initial state creation."""
    content = Path("api/main.py").read_text(encoding="utf-8")
    # All 3 places extract social_data_context
    assert content.count('collected_pool.get("social_data_context")') == 3
