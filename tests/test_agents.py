import pytest
from pydantic import ValidationError

from factory.agents import AgentCatalog
from tests.conftest import REPO_ROOT, make_spec


def test_repo_agent_definitions_load():
    catalog = AgentCatalog(REPO_ROOT / "agents")
    assert {"planner", "implementer", "reviewer", "integrator"} <= set(catalog.names())
    for spec in catalog:
        assert catalog.system_prompt(spec).strip()


def test_submit_result_required():
    with pytest.raises(ValidationError, match="submit_result"):
        make_spec(tools=["read_file"])


def test_unknown_output_schema_rejected():
    with pytest.raises(ValidationError, match="unknown output schema"):
        make_spec(output_schema="Nope")
