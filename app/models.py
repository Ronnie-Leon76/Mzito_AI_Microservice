import time
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class CliqUser(BaseModel):
    id: str | None = None
    name: str = 'User'
    email: EmailStr | None = None


class CliqIncomingMessage(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    user: CliqUser
    chat_id: str | None = None
    raw: dict = Field(default_factory=dict)


class TicketDraft(BaseModel):
    subject: str
    description: str
    requester_email: EmailStr
    priority: int = 1
    status: int = 2
    source: int = 2
    group_id: int | None = None
    category: str | None = None


class KBArticle(BaseModel):
    id: int
    title: str
    description: str = ''
    url: str | None = None


ConversationState = Literal['idle', 'awaiting_ticket_description', 'awaiting_ticket_confirmation']


class ConversationSession(BaseModel):
    """Per-user, per-chat short-lived state for multi-turn flows.

    Kept intentionally small: no ticket content is retained once the ticket
    is created or the session expires (see session_ttl_seconds).
    """
    key: str
    state: ConversationState = 'idle'
    pending_subject: str | None = None
    no_kb_result_streak: int = 0
    updated_at: float = Field(default_factory=time.time)

    def is_expired(self, ttl_seconds: int) -> bool:
        return (time.time() - self.updated_at) > ttl_seconds
