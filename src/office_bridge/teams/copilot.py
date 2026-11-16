"""At-most-once Copilot questions through the launcher-owned hidden frame."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from office_bridge.teams import session


class CopilotError(RuntimeError):
    pass


class CopilotStatus(BaseModel):
    state: Literal["ready", "not_ready"]
    work_iq_enabled: bool
    composer_nonempty: bool
    model: str | None = None


class ModelOption(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    selected: bool
    enabled: bool


class ModelCatalog(BaseModel):
    model: str | None
    models: list[ModelOption] = Field(max_length=30)


class QueryReceipt(BaseModel):
    request_id: str
    status: Literal["not_submitted", "awaiting_response", "completed"]
    submission_verified: bool
    model: str | None = None


class Citation(BaseModel):
    title: str = Field(max_length=500)
    url: str = Field(pattern=r"^https?://", max_length=4000)


class QueryResult(QueryReceipt):
    answer: str | None = Field(default=None, max_length=32000)
    citations: list[Citation] = Field(default_factory=list, max_length=50)
    work_iq_enabled: bool
    conversation_url: str | None = Field(default=None, pattern=r"^https://", max_length=4000)
    response_id: str | None = Field(default=None, max_length=200)
    citations_complete: bool = False


def _invoke(operation: str, arguments: dict) -> dict:
    try:
        return session.invoke(operation, arguments, timeout=100)
    except session.SessionError as error:
        raise CopilotError(f"Copilot operation stopped: {error}.") from None


def status() -> CopilotStatus:
    return CopilotStatus.model_validate(_invoke("teams_copilot_status", {}))


def models() -> ModelCatalog:
    value = _invoke("teams_copilot_models", {})
    if value.get("state") != "models":
        raise CopilotError("Copilot model choices could not be verified.")
    return ModelCatalog.model_validate(value)


def select_model(model: str) -> ModelCatalog:
    available = models()
    if sum(item.name == model and item.enabled for item in available.models) != 1:
        raise CopilotError("The requested model is not an available enabled Copilot choice.")
    value = _invoke("teams_copilot_models", {"model": model})
    if value.get("state") != "models" or value.get("model") != model:
        raise CopilotError("The selected Copilot model could not be verified.")
    result = ModelCatalog.model_validate(value)
    if sum(item.name == model and item.selected for item in result.models) != 1:
        raise CopilotError("The selected Copilot model could not be verified.")
    return result


def _path(request_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", request_id):
        raise CopilotError("Invalid Copilot request ID.")
    root = Path(os.environ["LOCALAPPDATA"]) / "OfficeActionServer" / "copilot-queries"
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or root.resolve() != root.absolute():
        raise CopilotError("Copilot records cannot be redirected.")
    return root / f"query-{request_id}.json"


def _save(path: Path, value: dict) -> None:
    handle, name = tempfile.mkstemp(prefix="query-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def ask(question: str, request_id: str, model: str | None = None) -> QueryReceipt:
    if not question.strip() or len(question) > 2000:
        raise CopilotError("Question must contain between 1 and 2000 characters.")
    path = _path(request_id)
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("question") != question or saved.get("requested_model") != model:
            raise CopilotError("This Copilot request ID belongs to another question or model.")
        if saved.get("result"):
            return QueryReceipt.model_validate(saved["result"])
        if saved.get("receipt"):
            return QueryReceipt.model_validate(saved["receipt"])
        if saved.get("state") != "not_submitted":
            raise CopilotError(
                "The previous attempt is uncertain; read its result without resubmitting."
            )
        observed = _invoke("teams_copilot_read", {"request_id": request_id})
        if (
            observed.get("state") != "not_submitted"
            or observed.get("question_matches") is not True
            or observed.get("work_iq_enabled") is not True
        ):
            raise CopilotError("The original unsubmitted draft changed; continuation is blocked.")
    else:
        ready = status()
        if ready.state != "ready" or not ready.work_iq_enabled or ready.composer_nonempty:
            raise CopilotError(
                "Copilot is unavailable, Work IQ is off, or an existing draft is present."
            )
        if (
            model is not None
            and sum(item.name == model and item.enabled for item in models().models) != 1
        ):
            raise CopilotError("The requested model is not an available enabled Copilot choice.")
        saved = {
            "question": question,
            "state": "reserved",
            "request_id": request_id,
            "requested_model": model,
        }
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump(saved, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            raise CopilotError(
                "This Copilot request ID is already reserved; do not resubmit."
            ) from None
    arguments = {"question": question, "request_id": request_id}
    if model is not None:
        arguments["model"] = model
    value = _invoke("teams_copilot_ask", arguments)
    if value.get("state") != "clicked" or value.get("request_id") != request_id:
        raise CopilotError(
            "Submission was not confirmed; inspect the existing query before retrying."
        )
    receipt = QueryReceipt(
        request_id=request_id,
        status="awaiting_response",
        submission_verified=False,
        model=value.get("model"),
    )
    saved.update(state="awaiting_response", receipt=receipt.model_dump())
    _save(path, saved)
    return receipt


def read(request_id: str) -> QueryResult:
    path = _path(request_id)
    if not path.exists():
        raise CopilotError("No local Copilot query has this request ID.")
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved.get("result"):
        return QueryResult.model_validate(saved["result"])
    value = _invoke("teams_copilot_read", {"request_id": request_id})
    if value.get("state") not in {"not_submitted", "awaiting_response", "completed"}:
        raise CopilotError(
            "The original Copilot query is unavailable; do not resubmit automatically."
        )
    result = QueryResult(
        request_id=request_id,
        status=value["state"],
        submission_verified=value.get("submission_verified", False),
        work_iq_enabled=value.get("work_iq_enabled", False),
        answer=value.get("answer"),
        citations=value.get("citations", []),
        conversation_url=value.get("conversation_url"),
        response_id=value.get("response_id"),
        citations_complete=value.get("citations_complete", False),
        model=value.get("model"),
    )
    if result.status == "not_submitted" and value.get("question_matches") is True:
        saved["state"] = "not_submitted"
        _save(path, saved)
    if result.status == "completed":
        if not result.submission_verified or not result.answer or not result.work_iq_enabled:
            raise CopilotError("Copilot response could not be verified.")
        saved.update(state="completed", result=result.model_dump())
        _save(path, saved)
    return result
