import logging
import re

from .cliq import kb_response, session_key, text_response
from .config import Settings
from .freshservice import FreshserviceClient, FreshserviceError, route_ticket
from .models import CliqIncomingMessage, ConversationSession, TicketDraft
from .session_store import SessionStore

logger = logging.getLogger('cliq-freshservice.service')

TICKET_RE = re.compile(r'^(?:create|raise|log|open)\s+(?:a\s+)?ticket\s*[:\-]?\s*(.*)$', re.I | re.S)
STATUS_RE = re.compile(r'^(?:ticket\s+)?status\s*[:#\-]?\s*(\d+)\s*$', re.I)
CANCEL_RE = re.compile(r'^(cancel|nevermind|never mind|stop)$', re.I)
CONFIRM_RE = re.compile(r'^(yes|y|confirm|create it)$', re.I)
DENY_RE = re.compile(r'^(no|n|cancel)$', re.I)
GREETING_TEXTS = {'help', 'menu', 'start', 'hi', 'hello'}
MIN_TICKET_DESCRIPTION_LENGTH = 8


class HelpdeskService:
    def __init__(self, settings: Settings, freshservice: FreshserviceClient, sessions: SessionStore):
        self.settings = settings
        self.freshservice = freshservice
        self.sessions = sessions

    async def handle(self, message: CliqIncomingMessage) -> dict:
        text = message.text.strip()
        key = session_key(message)
        session = await self.sessions.get(key) or ConversationSession(key=key)

        if text.lower() in GREETING_TEXTS:
            await self.sessions.delete(key)
            return self._help_text(message)

        if CANCEL_RE.match(text) and session.state != 'idle':
            await self.sessions.delete(key)
            return text_response('Okay, cancelled. Let me know if there is anything else I can help with.')

        # Mid-flow: we already asked for a ticket description.
        if session.state == 'awaiting_ticket_description':
            return await self._continue_ticket_creation(message, session, text)
        if session.state == 'awaiting_ticket_confirmation':
            return await self._confirm_ticket_creation(message, session, text)

        status_match = STATUS_RE.match(text)
        if status_match:
            return await self._ticket_status(message, int(status_match.group(1)))

        ticket_match = TICKET_RE.match(text)
        if ticket_match:
            return await self._start_or_create_ticket(message, session, ticket_match.group(1).strip())

        return await self._search_knowledge_base(message, session, text)

    def _help_text(self, message: CliqIncomingMessage) -> dict:
        return text_response(
            f'Hello {message.user.name}. I can search helpdesk articles and create Freshservice tickets.\n\n'
            'Examples:\n'
            '\u2022 BC password is not working\n'
            '\u2022 create ticket: Unable to sign in to D&S GO\n'
            '\u2022 status 12345\n\n'
            'Say "cancel" at any point to stop a ticket you are in the middle of creating.'
        )

    async def _search_knowledge_base(self, message: CliqIncomingMessage, session: ConversationSession, text: str) -> dict:
        try:
            articles = await self.freshservice.search_solutions(text, self.settings.kb_result_limit)
        except FreshserviceError:
            logger.exception('search_solutions failed')
            return text_response('The helpdesk service is temporarily unavailable. Please try again or use the Freshservice portal.')

        if articles:
            session.no_kb_result_streak = 0
            await self.sessions.set(session)
            return kb_response(text, articles)

        session.no_kb_result_streak += 1
        await self.sessions.set(session)
        if session.no_kb_result_streak >= self.settings.no_kb_result_streak_before_escalation:
            return text_response(
                f'I still could not find an article for \u201c{text}\u201d. '
                'Reply with "create ticket: <description>" and I will log this with the service desk right away.'
            )
        return kb_response(text, articles)

    async def _start_or_create_ticket(self, message: CliqIncomingMessage, session: ConversationSession, description: str) -> dict:
        if not message.user.email:
            return text_response(
                'I cannot create the ticket because Cliq did not provide your email address. '
                'Ask the Cliq administrator to expose the signed-in user email to this bot.'
            )
        if len(description) >= MIN_TICKET_DESCRIPTION_LENGTH:
            return await self._create_ticket(message, session, description)

        session.state = 'awaiting_ticket_description'
        await self.sessions.set(session)
        return text_response('Sure -- what is the issue? Please describe it in a sentence or two.')

    async def _continue_ticket_creation(self, message: CliqIncomingMessage, session: ConversationSession, text: str) -> dict:
        if len(text) < MIN_TICKET_DESCRIPTION_LENGTH:
            return text_response('That is a bit short -- could you add a little more detail about the issue? (or say "cancel")')
        return await self._create_ticket(message, session, text)

    async def _create_ticket(self, message: CliqIncomingMessage, session: ConversationSession, description: str) -> dict:
        subject = description.splitlines()[0][:100]
        group_id, category = route_ticket(description, self.settings.ticket_routing_rules)
        draft = TicketDraft(
            subject=subject,
            description=(
                f'<p>{description}</p>'
                f'<p><strong>Submitted from:</strong> Zoho Cliq</p>'
                f'<p><strong>Requester:</strong> {message.user.name} ({message.user.email})</p>'
            ),
            requester_email=message.user.email,
            priority=self.settings.default_ticket_priority,
            status=self.settings.default_ticket_status,
            source=self.settings.default_ticket_source,
            group_id=group_id,
            category=category,
        )
        try:
            ticket = await self.freshservice.create_ticket(draft)
        except FreshserviceError:
            logger.exception('create_ticket failed')
            return text_response('I could not create the ticket. Please use the Freshservice portal or contact the Service Delivery team.')
        finally:
            await self.sessions.delete(session.key)

        ticket_id = ticket.get('id')
        portal_url = f'{self.settings.freshservice_base_url}/support/tickets/{ticket_id}' if ticket_id else self.settings.freshservice_base_url
        return text_response(f'Ticket #{ticket_id} has been created successfully.\nSubject: {subject}\n{portal_url}')

    async def _confirm_ticket_creation(self, message: CliqIncomingMessage, session: ConversationSession, text: str) -> dict:
        # Reserved for future use if you want an explicit yes/no step before
        # filing (e.g. once you add category selection). Currently unreachable
        # since _start_or_create_ticket files immediately once it has a
        # description, but kept so that flow is a one-line change to enable.
        if CONFIRM_RE.match(text) and session.pending_subject:
            return await self._create_ticket(message, session, session.pending_subject)
        await self.sessions.delete(session.key)
        return text_response('Okay, cancelled.')

    async def _ticket_status(self, message: CliqIncomingMessage, ticket_id: int) -> dict:
        try:
            ticket = await self.freshservice.get_ticket(ticket_id)
        except FreshserviceError:
            logger.exception('get_ticket failed')
            return text_response(f'I could not retrieve ticket #{ticket_id}. Confirm the number or use the Freshservice portal.')

        # Authorisation: only the requester who raised the ticket may see its
        # details through the bot. Without this check, "status <any id>" would
        # leak subjects/status for every ticket in the instance to anyone who
        # can message the bot.
        requester_email = (ticket.get('requester_email') or '').lower()
        if not message.user.email:
            return text_response('I cannot verify who you are, so I cannot share ticket details here. Please use the Freshservice portal.')
        if requester_email and message.user.email.lower() != requester_email:
            logger.warning('Ticket status denied: requester mismatch for ticket %s', ticket_id)
            return text_response(f'Ticket #{ticket_id} was not raised by your account, so I cannot share its details here.')

        status_names = {2: 'Open', 3: 'Pending', 4: 'Resolved', 5: 'Closed'}
        priority_names = {1: 'Low', 2: 'Medium', 3: 'High', 4: 'Urgent'}
        return text_response(
            f'Ticket #{ticket_id}\n'
            f'Subject: {ticket.get("subject", "Not available")}\n'
            f'Status: {status_names.get(ticket.get("status"), ticket.get("status", "Unknown"))}\n'
            f'Priority: {priority_names.get(ticket.get("priority"), ticket.get("priority", "Unknown"))}'
        )
