"""Outlook operations that need durable coordination across Action Server workers."""

from office_bridge import desktop
from office_bridge.outlook import ledger
from office_bridge.outlook.models import MessageRef, SendReceipt


def send_draft(draft: MessageRef, expected_digest: str) -> SendReceipt:
    """Submit once and require the persistent host's Sent Items observation."""
    if "send_draft" not in desktop.OUTLOOK_SUPPORTED:
        raise desktop.BridgeError("outlook_operation_unavailable")
    reference = draft.model_dump(mode="json")

    def submit() -> dict:
        return desktop.invoke(
            "send_draft", {"draft": reference, "expected_digest": expected_digest}
        )

    try:
        result = ledger.send_once(reference, expected_digest, submit)
    except ledger.SendLedgerError as error:
        raise desktop.BridgeError(error.code) from None
    return SendReceipt.model_validate(result)
