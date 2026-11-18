"""One script-free reading view for Office results and the MCP App host."""

from datetime import datetime
from html import escape
from pathlib import Path
from typing import Annotated, Generic, Literal, TypeVar
from uuid import uuid4

from actions import Response
from pydantic import BaseModel, Field

MESSAGE_URI = "ui://office-bridge/messages-v1.html"
MESSAGE_UI = {"ui": {"resourceUri": MESSAGE_URI, "visibility": ["model", "app"]}}
T = TypeVar("T")


class Card(BaseModel):
    kind: Literal["mail", "chat", "receipt"] | None = None
    heading: str
    detail: str = ""
    body: str = ""
    unread: bool | None = None
    timestamp: str = ""
    facts: dict[str, str] = Field(default_factory=dict)
    body_truncated: bool = False


class View(BaseModel):
    source: Literal["Outlook", "Teams", "Office"] = "Office"
    title: str = "Messages"
    detail: str = ""
    empty: str = "No messages found"
    cards: Annotated[list[Card], Field(max_length=50)]


class Presentation(BaseModel):
    view_id: str = Field(default_factory=lambda: uuid4().hex)
    view: View
    html: str


class MessageResponse(Response[T], Generic[T]):
    presentation: Presentation | None = None


def _template() -> str:
    return Path(__file__).with_name("message_view.html").read_text(encoding="utf-8")


def app_html() -> str:
    script = Path(__file__).with_name("message_app.js").read_text(encoding="utf-8")
    return _template().replace("<!--host-->", f"<script>{script}</script>")


def readable_time(value: str) -> str:
    try:
        date = datetime.fromisoformat(value)
    except ValueError:
        return value  # Teams also returns localized, already readable time labels.
    if date.tzinfo is not None:
        date = date.astimezone()
    hour = date.strftime("%I:%M %p").lstrip("0")
    zone = date.strftime("%z")
    offset = f" UTC{zone[:3]}:{zone[3:]}" if zone else ""
    return f"{date:%b} {date.day}, {date.year} · {hour}{offset}"


def _article(card: Card, *, listing: bool) -> str:
    kind = card.kind or "message"
    heading = f"<h2>{escape(card.heading)}</h2>"
    who = f'<p class="detail">{escape(card.detail)}</p>' if card.detail else ""
    when = (
        f'<span class="when" title="{escape(card.timestamp)}">'
        f"{escape(readable_time(card.timestamp))}</span>"
        if card.timestamp
        else ""
    )
    unread = '<span class="unread">Unread</span>' if card.unread else ""
    header = f'<div class="row-head">{who}{when}{unread}</div>'
    facts = "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in card.facts.items())
    content = f"<dl>{facts}</dl>" if facts else ""
    if card.body:
        body = f'<div class="body">{escape(card.body)}</div>'
        if kind in {"chat", "receipt"} and (len(card.body) > 400 or card.body.count("\n") > 6):
            preview = "\n".join(card.body.splitlines()[:3])[:280].rstrip()
            body = (
                f'<p class="preview">{escape(preview)}…</p><details class="disclosure">'
                f"<summary>Full message</summary>{body}</details>"
            )
        content += body
    if card.body_truncated:
        content += '<p class="note">Body truncated by Outlook</p>'
    if listing and content:
        # Native, keyboard-operable single selection; no mailbox calls on expansion.
        row = (
            f'<details class="mail-row" name="inbox"><summary>{header}{heading}</summary>'
            f'<div class="expanded">{content}</div></details>'
        )
    else:
        row = header + heading + content
    if kind == "chat":
        initials = "".join(word[0] for word in card.heading.split()[:2])
        row = f'<span class="avatar" aria-hidden="true">{escape(initials)}</span><div>{row}</div>'
    return (
        f'<article class="{kind}" data-unread="{str(card.unread is True).lower()}">{row}</article>'
    )


def present(view: View) -> Presentation:
    receipt = bool(view.cards) and all(card.kind == "receipt" for card in view.cards)
    mode = (
        "receipt"
        if receipt
        else {"Teams": "teams", "Outlook": "outlook"}.get(view.source, "office")
    )
    source = {
        "Teams": "Microsoft Teams",
        "Outlook": "Microsoft Outlook",
        "Office": "Office Bridge",
    }[view.source]
    scope = f'<p class="scope">{escape(view.detail)}</p>' if view.detail else ""
    header = (
        f'<header class="mast {mode}"><div class="mast-copy"><p class="source">{source}</p>'
        f"<h1>{escape(view.title)}</h1>{scope}</div>"
        '<div class="glyph" aria-hidden="true"><i class="back"></i><i class="paper"></i>'
        '<i class="front"></i></div></header>'
    )
    unread = sum(card.unread is True for card in view.cards)
    controls = ""
    if unread and any(card.unread is False for card in view.cards):
        controls = (
            f'<label class="filter"><input type="checkbox">Unread only '
            f'<span class="count">{unread}</span></label>'
        )
    listing = len(view.cards) > 1 and all(card.kind == "mail" for card in view.cards)
    articles = "".join(_article(card, listing=listing) for card in view.cards)
    content = (
        header
        + controls
        + (
            f'<section class="rows" aria-label="Messages">{articles}</section>'
            if articles
            else f'<p class="empty">{escape(view.empty)}</p>'
        )
    )
    return Presentation(view=view, html=_template().replace("<!--messages-->", content))


def _mail(item: dict) -> Card:
    sender = item["sender"]
    facts = {}
    if sender.get("smtp_address") and sender.get("display_name"):
        facts["From"] = sender["smtp_address"]
    recipients = item.get("recipients", [])
    for label, category in (("To", 1), ("Cc", 2), ("Bcc", 3)):
        names = [
            r.get("display_name") or r.get("smtp_address")
            for r in recipients
            if r.get("recipient_type", 1) == category
        ]
        if any(names):
            facts[label] = ", ".join(filter(None, names))
    if item.get("importance"):
        facts["Importance"] = item["importance"].capitalize()
    if item.get("flagged"):
        facts["Follow up"] = "Flagged"
    return Card(
        kind="mail",
        heading=item["subject"] or "(No subject)",
        detail=sender.get("display_name") or sender.get("smtp_address") or "",
        timestamp=item["received_at"],
        body=item.get("body_text", ""),
        unread=item.get("unread"),
        facts=facts,
        body_truncated=item.get("body_truncated", False),
    )


def message_view(result: dict, *, title: str = "Inbox", scope: str = "") -> View:
    if "items" in result:
        cards = [_mail(item) for item in result["items"]]
        count = len(cards)
        detail = [f"{count} {'message' if count == 1 else 'messages'}"]
        if "scanned_count" in result:
            detail.append(f"{result['scanned_count']} scanned")
        if result["truncated"]:
            detail.append("Partial inbox results")
        if scope:
            detail.append(scope)
        return View(
            source="Outlook",
            title=title,
            detail=" · ".join(detail),
            cards=cards,
            empty="No matches in the scanned inbox items"
            if result["truncated"]
            else "No messages found",
        )
    if "sender" in result:
        return View(source="Outlook", title="Mail", cards=[_mail(result)])
    if "messages" in result and "chat" in result:
        title = result["chat"].get("title") or result["chat"]["exact_tree_label"]
        cards = [
            Card(
                kind="chat",
                heading=m.get("author") or title,
                timestamp=m.get("time", ""),
                body=m["text"],
            )
            for m in result["messages"]
        ]
        detail = f"{len(cards)} loaded {'message' if len(cards) == 1 else 'messages'}"
        if result.get("truncated"):
            detail += " · Partial chat history"
        if result.get("captured_at"):
            detail += f" · Captured {readable_time(result['captured_at'])}"
        return View(
            source="Teams",
            title=title,
            detail=detail,
            cards=cards,
            empty="No messages in the loaded history",
        )
    status = result.get("status")
    if status in ("observed", "already_observed") and "chat" in result:
        outcome = "Observed in Teams" if status == "observed" else "Previously observed in Teams"
        return View(
            source="Teams",
            title=outcome,
            cards=[
                Card(
                    kind="receipt",
                    heading=result["chat"]["title"],
                    detail="Conversation",
                    timestamp=result["time"],
                )
            ],
        )
    if status in ("submitted", "already_submitted") and "draft" in result:
        return View(
            source="Outlook",
            title="Submitted to Outlook"
            if status == "submitted"
            else "Previously submitted to Outlook",
            cards=[
                Card(
                    kind="receipt",
                    heading=result.get("subject") or "Mail submission",
                    timestamp=result["submitted_at"],
                    body=result.get("body_text") or "",
                    body_truncated=result.get("body_truncated", False),
                )
            ],
        )
    raise ValueError("Unrecognized Office message result")


def with_presentation(response: Response[T], **context: str) -> MessageResponse[T]:
    display = None
    if response.result is not None and not response.error:
        try:
            display = present(message_view(response.result.model_dump(mode="json"), **context))
        except Exception:
            # Rendering is advisory even after a successful, non-repeatable send.
            pass
    return MessageResponse(result=response.result, error=response.error, presentation=display)
