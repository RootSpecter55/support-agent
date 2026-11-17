from app.core.errors import ConflictError
from app.modules.conversations.models import Conversation, ConversationStatus

S = ConversationStatus

# State machine: a conversation has a real lifecycle, so every allowed move is listed here and
# any other is refused. RESPONDING also guarantees one agent turn at a time per conversation.
ALLOWED_TRANSITIONS: dict[ConversationStatus, frozenset[ConversationStatus]] = {
    S.ACTIVE: frozenset({S.RESPONDING}),
    S.RESPONDING: frozenset({S.ACTIVE, S.AWAITING_APPROVAL, S.ESCALATED}),
    S.AWAITING_APPROVAL: frozenset({S.RESPONDING}),
    S.ESCALATED: frozenset(),  # a person has taken over
}


class InvalidTransitionError(ConflictError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"Conversation cannot move from {current} to {target}")
        self.code = "INVALID_STATE"


def transition(conversation: Conversation, target: ConversationStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[ConversationStatus(conversation.status)]:
        raise InvalidTransitionError(conversation.status, target)
    conversation.status = target
