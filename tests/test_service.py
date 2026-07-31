import pytest

from app.models import CliqIncomingMessage, CliqUser
from app.service import HelpdeskService
from app.session_store import InMemorySessionStore


class FakeSettings:
    app_name = 'Test'
    kb_result_limit = 3
    default_ticket_priority = 1
    default_ticket_status = 2
    default_ticket_source = 2
    freshservice_base_url = 'https://example.freshservice.com'
    ticket_routing_rules: dict = {}
    no_kb_result_streak_before_escalation = 2
    escalation_group_id = None


class FakeFreshservice:
    def __init__(self, articles=None, ticket_id=101, requester_email=None):
        self.articles = articles or []
        self.ticket_id = ticket_id
        self.requester_email = requester_email
        self.created_drafts = []

    async def search_solutions(self, query, limit):
        return self.articles

    async def create_ticket(self, draft):
        self.created_drafts.append(draft)
        return {'id': self.ticket_id, 'subject': draft.subject}

    async def get_ticket(self, ticket_id):
        return {
            'id': ticket_id,
            'subject': 'Login issue',
            'status': 2,
            'priority': 2,
            'requester_email': self.requester_email,
        }


def make_service(freshservice=None):
    return HelpdeskService(FakeSettings(), freshservice or FakeFreshservice(), InMemorySessionStore(ttl_seconds=600))


@pytest.mark.asyncio
async def test_create_ticket_with_full_description():
    service = make_service()
    message = CliqIncomingMessage(
        text='create ticket: Unable to log in to BC',
        user=CliqUser(name='Loise', email='loise@example.com'),
    )
    result = await service.handle(message)
    assert 'Ticket #101' in result['text']


@pytest.mark.asyncio
async def test_create_ticket_multi_turn_when_description_too_short():
    service = make_service()
    user = CliqUser(name='Loise', email='loise@example.com')

    first = await service.handle(CliqIncomingMessage(text='create ticket', user=user, chat_id='chat-1'))
    assert 'what is the issue' in first['text'].lower()

    second = await service.handle(
        CliqIncomingMessage(text='I cannot log in to Business Central since this morning', user=user, chat_id='chat-1')
    )
    assert 'Ticket #101' in second['text']


@pytest.mark.asyncio
async def test_create_ticket_cancel_mid_flow():
    service = make_service()
    user = CliqUser(name='Loise', email='loise@example.com')
    await service.handle(CliqIncomingMessage(text='create ticket', user=user, chat_id='chat-2'))
    cancelled = await service.handle(CliqIncomingMessage(text='cancel', user=user, chat_id='chat-2'))
    assert 'cancelled' in cancelled['text'].lower()


@pytest.mark.asyncio
async def test_create_ticket_requires_email():
    service = make_service()
    message = CliqIncomingMessage(text='create ticket: printer is broken', user=CliqUser(name='Loise'))
    result = await service.handle(message)
    assert 'email address' in result['text'].lower()


@pytest.mark.asyncio
async def test_status_returns_details_when_requester_matches():
    fs = FakeFreshservice(requester_email='loise@example.com')
    service = make_service(fs)
    message = CliqIncomingMessage(text='status 101', user=CliqUser(name='Loise', email='loise@example.com'))
    result = await service.handle(message)
    assert 'Status: Open' in result['text']


@pytest.mark.asyncio
async def test_status_denied_for_other_users_ticket():
    fs = FakeFreshservice(requester_email='someoneelse@example.com')
    service = make_service(fs)
    message = CliqIncomingMessage(text='status 101', user=CliqUser(name='Loise', email='loise@example.com'))
    result = await service.handle(message)
    assert 'not raised by your account' in result['text'].lower()


@pytest.mark.asyncio
async def test_kb_search_escalates_after_repeated_misses():
    service = make_service(FakeFreshservice(articles=[]))
    user = CliqUser(name='Loise', email='loise@example.com')
    await service.handle(CliqIncomingMessage(text='obscure issue one', user=user, chat_id='chat-3'))
    second = await service.handle(CliqIncomingMessage(text='obscure issue one', user=user, chat_id='chat-3'))
    assert 'create ticket' in second['text'].lower()
