import json

from factory.schemas import EXPORTED_SCHEMAS
from tests.conftest import REPO_ROOT


def test_exported_schemas_are_in_sync():
    """Run `factory schemas export` after changing factory/schemas.py."""
    for name, model in EXPORTED_SCHEMAS.items():
        on_disk = json.loads((REPO_ROOT / "schemas" / f"{name}.json").read_text())
        assert on_disk == model.model_json_schema(), f"schemas/{name}.json is stale"


def test_repo_gates_config_is_valid():
    from factory.harness.gates import GateRunner

    runner = GateRunner.from_workspace(REPO_ROOT)
    assert {"build", "unit_tests", "lint"} <= set(runner.commands)
