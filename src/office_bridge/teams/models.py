"""Desktop and background Teams request/response schemas."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

from office_bridge.models import Identifier, Model


class ChatRef(Model):
    snapshot_id: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    exact_tree_label: Identifier
    window_title: Identifier


class ChatSummary(Model):
    ref: ChatRef
    display_name: str


class ChatPage(Model):
    items: list[ChatSummary]
    scope: Literal["currently_loaded_chat_list"]
    captured_at: datetime
    truncated: bool


class ChatMessage(Model):
    observed_id: str
    text: str


class ChatHistory(Model):
    chat: ChatRef
    messages: list[ChatMessage]
    scope: Literal["currently_loaded_history"]
    captured_at: datetime
    truncated: bool


class TeamsWebStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal[
        "authenticated", "needs_interactive_login", "access_blocked", "loading", "failed"
    ]
    hostname: str
    path: str
    browser_engine: Literal["chromium", "msedge", "webview2"]
    backend: Literal["chromium", "msedge", "webview2"]
    account_verified: bool | None = None
    host_visible: bool | None = None
    audio_muted: bool | None = None
    control_visible: bool | None = None
    host_focused: bool | None = None
    host_owned: bool | None = None
    foreground_unchanged: bool | None = None
    foreground_interference: bool | None = None
    generation: str | None = None
    restart_required: bool | None = None
    capabilities: list[str] = Field(default_factory=list)
    samples: list[dict] | None = None
    reason: (
        Literal[
            "browser_attachment_denied",
            "profile_busy",
            "navigation_failed",
            "browser_cleanup_failed",
            "browser_failed",
            "account_unverified",
            "host_visible",
            "foreground_interference",
            "sdk_unavailable",
            "timeout",
            "desktop_operation_failed",
            "windows_required",
            "webview2_failed",
            "bridge_failed",
            "invalid_bridge_result",
            "output_overflow",
            "session_unavailable",
            "session_timeout",
            "session_disconnected",
            "host_operation_failed",
            "host_unavailable",
            "session_write_failed",
            "invalid_response",
        ]
        | None
    ) = None


class WebChatMessage(BaseModel):
    id: str
    text: str
    author: str
    time: str


class WebChatRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    chat_id: Annotated[
        str,
        Field(
            pattern=r"^(?:48:notes|19:[A-Za-z0-9._:-]{1,256}@(?:thread\.(?:v2|skype)|unq\.gbl\.spaces))$"
        ),
    ]
    title: Annotated[str, Field(min_length=1, max_length=120)]


class WebChatSearchResult(BaseModel):
    items: list[WebChatRef]
    scope: Literal["currently_loaded_chat_list", "teams_search"]
    truncated: bool


class MentionRecipient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Annotated[str, Field(min_length=3, max_length=320)]

    @field_validator("email")
    @classmethod
    def stable_email_shape(cls, value: str) -> str:
        if value != value.strip() or any(char.isspace() for char in value) or value.count("@") != 1:
            raise ValueError("Recipient email must be a complete stable address.")
        local, domain = value.split("@")
        if (
            not local
            or not domain
            or "." not in domain
            or domain.startswith(".")
            or domain.endswith(".")
        ):
            raise ValueError("Recipient email must be a complete stable address.")
        return value


class PersonMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: Annotated[str, Field(pattern=r"^[a-z0-9-]{1,32}$")]
    display_label: Annotated[str, Field(min_length=1, max_length=120)]
    recipient: MentionRecipient

    @field_validator("display_label")
    @classmethod
    def display_label_is_single_line(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("Mention display label must be trimmed single-line text.")
        return value


class PersonMentions(RootModel[list[PersonMention]]):
    root: Annotated[list[PersonMention], Field(max_length=10)]

    @model_validator(mode="after")
    def unique_keys_and_recipients(self):
        keys = [mention.key for mention in self.root]
        recipients = [mention.recipient.email.casefold() for mention in self.root]
        if len(keys) != len(set(keys)):
            raise ValueError("Mention keys must be unique.")
        if len(recipients) != len(set(recipients)):
            raise ValueError("Each recipient may be mentioned only once.")
        return self


class PersonMentionReceipt(BaseModel):
    """One observed Teams mention entity; notification delivery is not verified."""

    key: str
    text: str
    recipient_email: str
    identity_evidence: str
    entity_verified: bool


class GroupReceipt(BaseModel):
    status: Literal["observed", "already_observed"]
    chat: WebChatRef
    message_id: str
    time: str
    mentions: list[PersonMentionReceipt] = Field(
        default_factory=list,
        exclude_if=lambda mentions: not mentions,
        description=(
            "Observed mention entities. This does not verify that Teams delivered notifications."
        ),
    )


class EditReceipt(BaseModel):
    status: Literal["edited", "already_edited"]
    chat: WebChatRef
    message_id: str
    time: str


class UploadReceipt(BaseModel):
    status: Literal["uploaded", "already_uploaded"]
    chat: WebChatRef
    message_id: Annotated[str, Field(pattern=r"^message-body-[0-9]{13}$")]
    time: str
    files: Annotated[list[str], Field(min_length=1, max_length=10)]


class UploadStatus(BaseModel):
    status: Literal["not_found", "outcome_unknown", "uploaded"]
    chat: WebChatRef
    request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")]
    file_count: Annotated[int, Field(ge=0, le=10)]
    receipt: UploadReceipt | None = None


class UploadReadiness(BaseModel):
    state: Literal["ready", "blocked"]
    chat: WebChatRef
    composer_empty: bool
    file_input_available: bool
    file_input_count: Annotated[int, Field(ge=0, le=10)]
    file_input_accepts: Annotated[str, Field(max_length=160)] = ""
    file_input_mode: Literal["direct", "attach_menu", "unavailable"] = "unavailable"
    file_chooser_interception_available: bool = False
    attach_control_available: bool = False
    reason: (
        Literal[
            "composer_has_draft",
            "attachments_pending",
            "file_input_unavailable",
            "identity_failed",
        ]
        | None
    ) = None


class WebChatHistory(BaseModel):
    chat: WebChatRef
    scope: Literal["currently_loaded_history"]
    messages: list[WebChatMessage]
