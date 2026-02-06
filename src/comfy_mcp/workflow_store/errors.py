"""Workflow store errors."""


class WorkflowStoreError(RuntimeError):
    """Base error for workflow store operations."""


class InvalidWorkflowIdError(WorkflowStoreError):
    """Raised when a workflow id is invalid or unsafe."""


class WorkflowNotFoundError(WorkflowStoreError):
    """Raised when a workflow cannot be found on disk."""
