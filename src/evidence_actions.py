"""Evidence packs and explicit, verified Devsy delivery."""

from typing import Annotated

from actions import ActionError, Response, action
from pydantic import Field

from office_bridge import evidence
from office_bridge.evidence import EvidenceDeliveryReceipt, EvidencePackInput, EvidencePackReceipt


@action(is_consequential=False)
def evidence_pack_create(
    pack: EvidencePackInput,
) -> Response[EvidencePackReceipt]:
    """Create a private evidence ZIP with a summary, source records and checksum manifest.

    Args:
        pack: Title, caller-authored Markdown summary, explicit sources and coverage limitations.
    """
    try:
        return Response(
            result=evidence.create_pack(pack.title, pack.summary, pack.sources, pack.coverage)
        )
    except evidence.EvidenceError as error:
        raise ActionError(str(error)) from None


@action(is_consequential=True)
def evidence_pack_send_devsy(
    pack_id: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")],
    workspace: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")],
    request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")],
) -> Response[EvidenceDeliveryReceipt]:
    """Deliver a specific evidence pack to a named Devsy workspace after user authorization.

    Args:
        pack_id: SHA-256 ID returned by evidence_pack_create.
        workspace: Exact workspace ID from the current Devsy inventory.
        request_id: Unique delivery ID; uncertain outcomes block automatic replay.
    """
    try:
        return Response(result=evidence.send_pack(pack_id, workspace, request_id))
    except evidence.EvidenceError as error:
        raise ActionError(str(error)) from None
