"""ParamSpec errors."""


class ParamSpecError(RuntimeError):
    """Base error for ParamSpec operations."""


class ParamValidationError(ParamSpecError):
    """Raised when parameter validation fails."""


class ParamPatchError(ParamSpecError):
    """Raised when patching fails."""
