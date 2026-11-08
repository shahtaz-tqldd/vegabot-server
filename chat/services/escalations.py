"""Escalation handling for queries the chatbot could not answer."""

from django.db import transaction

from chat.models import ChatEscalation
from chat.utils.choices import EscalationReason, EscalationStatus


@transaction.atomic
def record_escalation(
    *,
    session,
    question,
    message=None,
    reason=EscalationReason.NO_ANSWER,
    created_by=None,
):
    """Store an escalation for an unanswered query.

    Duplicate open escalations for the same question in one session are
    idempotent: the existing escalation is returned.
    """
    question = (question or "").strip()
    if not question:
        raise ValueError("An escalation requires the unanswered question.")

    escalation = (
        ChatEscalation.objects.select_for_update()
        .filter(
            chat_session=session,
            question=question,
            status=EscalationStatus.OPEN,
        )
        .first()
    )
    if escalation is not None:
        return escalation

    return ChatEscalation.objects.create(
        chat_session=session,
        chat_message=message,
        question=question,
        reason=reason,
        created_by=created_by or session.created_by,
    )


def resolve_escalation(escalation, *, resolution_note="", resolved_by=None):
    """Mark an escalation resolved."""
    escalation.status = EscalationStatus.RESOLVED
    escalation.resolution_note = resolution_note
    if resolved_by is not None:
        escalation.updated_by = resolved_by
    escalation.save(
        update_fields=["status", "resolution_note", "updated_by", "updated_at"],
    )
    return escalation
