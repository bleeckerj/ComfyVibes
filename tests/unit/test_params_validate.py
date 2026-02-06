"""Tests for ParamSpec validation."""

import pytest

from comfy_mcp.params.errors import ParamValidationError
from comfy_mcp.params.schema import ParamConstraints, ParamSpec, ParamSpecItem, ParamTarget
from comfy_mcp.params.validate import validate_overrides


def _build_spec() -> ParamSpec:
    return ParamSpec(
        schema_version="1",
        workflow_id="demo",
        workflow_hash="hash",
        params=[
            ParamSpecItem(
                name="seed",
                type="int",
                required=True,
                target=ParamTarget(mode="direct", node_id="1", input="seed"),
            ),
            ParamSpecItem(
                name="prompt",
                type="string",
                required=False,
                target=ParamTarget(mode="direct", node_id="2", input="text"),
            ),
            ParamSpecItem(
                name="cfg",
                type="float",
                required=False,
                constraints=ParamConstraints(min=1.0, max=10.0),
                target=ParamTarget(mode="direct", node_id="1", input="cfg"),
            ),
            ParamSpecItem(
                name="sampler",
                type="enum",
                required=False,
                constraints=ParamConstraints(choices=["euler", "ddim"]),
                target=ParamTarget(mode="direct", node_id="1", input="sampler_name"),
            ),
        ],
    )


def test_validate_rejects_unknown_param() -> None:
    """Validation should reject unknown parameters."""
    print("Test: validate_overrides should reject unknown param names.")
    spec = _build_spec()

    with pytest.raises(ParamValidationError):
        validate_overrides(spec, {"unknown": 1})


def test_validate_requires_required_params() -> None:
    """Validation should enforce required params."""
    print("Test: validate_overrides should enforce required params.")
    spec = _build_spec()

    with pytest.raises(ParamValidationError):
        validate_overrides(spec, {"prompt": "hello"})


def test_validate_type_mismatch() -> None:
    """Validation should reject wrong types."""
    print("Test: validate_overrides should reject type mismatches.")
    spec = _build_spec()

    with pytest.raises(ParamValidationError):
        validate_overrides(spec, {"seed": "not-int"})


def test_validate_constraints() -> None:
    """Validation should enforce min/max and enum choices."""
    print("Test: validate_overrides should enforce constraints.")
    spec = _build_spec()

    with pytest.raises(ParamValidationError):
        validate_overrides(spec, {"seed": 1, "cfg": 100.0})

    with pytest.raises(ParamValidationError):
        validate_overrides(spec, {"seed": 1, "sampler": "invalid"})


def test_validate_success() -> None:
    """Validation should accept valid overrides."""
    print("Test: validate_overrides should accept valid overrides.")
    spec = _build_spec()

    overrides, _ = validate_overrides(spec, {"seed": 2, "cfg": 2.5, "sampler": "euler"})

    assert overrides["seed"] == 2
