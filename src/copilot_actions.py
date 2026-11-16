"""Microsoft 365 Copilot Work IQ through the persistent Teams session."""

from typing import Annotated

from actions import ActionError, Response, action
from pydantic import Field

from office_bridge.teams import copilot
from office_bridge.teams.copilot import CopilotStatus, ModelCatalog, QueryReceipt, QueryResult


def _respond(operation, *arguments):
    try:
        return Response(result=operation(*arguments))
    except copilot.CopilotError as error:
        raise ActionError(str(error)) from None


@action(is_consequential=False)
def teams_copilot_status() -> Response[CopilotStatus]:
    """Check the hidden Copilot pane and Work IQ without submitting a question."""
    return _respond(copilot.status)


@action(is_consequential=True)
def teams_copilot_ask(
    question: Annotated[str, Field(min_length=1, max_length=2000)],
    request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")],
    model: Annotated[str | None, Field(min_length=1, max_length=120)] = None,
) -> Response[QueryReceipt]:
    """Submit one authorized question to default Microsoft 365 Copilot with Work IQ on.

    Args:
        question: User-authorized question. This sends content to Microsoft 365 Copilot.
        request_id: Unique ID for the question; uncertain submissions cannot be replayed.
        model: Exact enabled name returned by teams_copilot_models; omit to keep the selected model.
    """
    return _respond(copilot.ask, question, request_id, model)


@action(is_consequential=False)
def teams_copilot_models() -> Response[ModelCatalog]:
    """Read the available model dropdown choices without changing the selected model."""
    return _respond(copilot.models)


@action(is_consequential=False)
def teams_copilot_select_model(
    model: Annotated[str, Field(min_length=1, max_length=120)],
) -> Response[ModelCatalog]:
    """Select an enabled Copilot dropdown choice without submitting a question.

    Args:
        model: Exact enabled name returned by teams_copilot_models.
    """
    return _respond(copilot.select_model, model)


@action(is_consequential=False)
def teams_copilot_read(
    request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")],
) -> Response[QueryResult]:
    """Read an existing Copilot query's settled response and observed citation links.

    Args:
        request_id: The original question ID; this action never submits another question.
    """
    return _respond(copilot.read, request_id)
