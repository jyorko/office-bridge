"""Read-only Teams web status action."""
from typing import Literal
from actions import Response, action
from pydantic import BaseModel, ConfigDict

import teams_web_backend


class TeamsWebStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["authenticated", "needs_interactive_login", "access_blocked", "failed"]
    hostname: str
    path: str
    reason: Literal["browser_attachment_denied"] | None = None


@action(is_consequential=False)
def teams_web_status() -> Response[TeamsWebStatus]:
    """Check Teams web authentication in the isolated headless Edge profile.

    Args:
        None.
    """
    return Response(result=TeamsWebStatus.model_validate(teams_web_backend.status()))
