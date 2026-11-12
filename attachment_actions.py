"""Download Outlook attachments into a bounded private local cache."""
from typing import Annotated

from actions import ActionError, Response
from actions.mcp import tool
from pydantic import Field

import desktop_bridge
from office_actions import Identifier, MessageRef, Model


class AttachmentDownload(Model):
    index: Annotated[int, Field(ge=1, le=500)]
    filename: str
    size_bytes: Annotated[int, Field(ge=0, le=52_428_800)]
    local_path: Annotated[str, Field(min_length=1)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class AttachmentDownloads(Model):
    ref: MessageRef
    items: Annotated[list[AttachmentDownload], Field(max_length=25)]


@tool(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
def outlook_download_attachments(entry_id: Identifier, store_id: Identifier, pdf_only: bool = False) -> Response[AttachmentDownloads]:
    """Save mail attachments locally without opening them, up to 25 files and 50 MiB.

    Args:
        entry_id: Exact message entry identity returned by an Outlook mail listing.
        store_id: Exact store identity returned with the message entry identity.
        pdf_only: Save only PDF attachments, excluding signature images and other files.
    """
    reference = MessageRef(entry_id=entry_id, store_id=store_id)
    try:
        result = desktop_bridge.invoke("download_attachments", {**reference.model_dump(), "pdf_only": pdf_only})
        return Response(result=AttachmentDownloads.model_validate(result))
    except desktop_bridge.BridgeError as error:
        raise ActionError(f"Attachment download failed: {error.code}.") from None
