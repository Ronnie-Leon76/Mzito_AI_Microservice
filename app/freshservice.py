import asyncio
import logging
import random
import re
from html import unescape
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from .config import Settings
from .models import KBArticle, TicketDraft

logger = logging.getLogger('cliq-freshservice.freshservice')

# Freshservice returns 429 with a Retry-After header when the org's API rate
# limit is hit; 5xx are transient. Everything else (4xx auth/validation) is
# not retried, since retrying won't change the outcome.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class FreshserviceError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class FreshserviceClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.freshservice_base_url,
            auth=(settings.freshservice_api_key, 'X'),
            timeout=settings.request_timeout_seconds,
            headers={'Accept': 'application/json'},
            transport=transport,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        attempt = 0
        last_error: FreshserviceError | None = None
        while attempt <= self.settings.freshservice_max_retries:
            attempt += 1
            try:
                response = await self.client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                last_error = FreshserviceError(f'Network error calling Freshservice: {exc}')
            else:
                if response.status_code < 400:
                    return response.json() if response.content else {}
                detail = response.text[:500]
                last_error = FreshserviceError(
                    f'Freshservice returned {response.status_code}: {detail}',
                    status_code=response.status_code,
                )
                if response.status_code not in _RETRYABLE_STATUS:
                    raise last_error

            if attempt > self.settings.freshservice_max_retries:
                break
            delay = self.settings.freshservice_retry_base_delay * (2 ** (attempt - 1))
            delay += random.uniform(0, delay * 0.25)  # jitter avoids retry storms
            logger.warning(
                'Freshservice call failed (attempt %s/%s), retrying in %.2fs: %s',
                attempt, self.settings.freshservice_max_retries + 1, delay, last_error,
            )
            await asyncio.sleep(delay)

        assert last_error is not None
        raise last_error

    @staticmethod
    def _plain_text(html: str | None) -> str:
        if not html:
            return ''
        text = BeautifulSoup(unescape(html), 'html.parser').get_text(' ', strip=True)
        return re.sub(r'\s+', ' ', text)

    async def search_solutions(self, query: str, limit: int = 3) -> list[KBArticle]:
        payload = await self._request('GET', f'/api/v2/search/solutions?term={quote_plus(query)}')
        records = payload.get('solutions') or payload.get('articles') or []
        results: list[KBArticle] = []
        for item in records[:limit]:
            article_id = int(item['id'])
            results.append(KBArticle(
                id=article_id,
                title=item.get('title') or item.get('name') or 'Knowledge article',
                description=self._plain_text(item.get('description') or item.get('description_text'))[:500],
                url=item.get('url') or f'{self.settings.freshservice_base_url}/support/solutions/articles/{article_id}',
            ))
        return results

    async def create_ticket(self, draft: TicketDraft) -> dict:
        body: dict = {
            'subject': draft.subject,
            'description': draft.description,
            'email': str(draft.requester_email),
            'priority': draft.priority,
            'status': draft.status,
            'source': draft.source,
        }
        if self.settings.freshservice_workspace_id:
            body['workspace_id'] = self.settings.freshservice_workspace_id
        if draft.group_id:
            body['group_id'] = draft.group_id
        if draft.category:
            body['category'] = draft.category
        payload = await self._request('POST', '/api/v2/tickets', json=body)
        return payload.get('ticket', payload)

    async def get_ticket(self, ticket_id: int) -> dict:
        # include=requester so the response carries requester_email, which
        # HelpdeskService uses to check that the Cliq user asking for status
        # actually owns the ticket before revealing anything about it.
        payload = await self._request('GET', f'/api/v2/tickets/{ticket_id}?include=requester')
        ticket = payload.get('ticket', payload)
        requester = ticket.get('requester') or {}
        if requester.get('email') and not ticket.get('requester_email'):
            ticket['requester_email'] = requester['email']
        return ticket

    async def add_note(self, ticket_id: int, body_html: str, private: bool = True) -> dict:
        payload = await self._request(
            'POST', f'/api/v2/tickets/{ticket_id}/notes', json={'body': body_html, 'private': private}
        )
        return payload.get('note', payload)


def route_ticket(description: str, routing_rules: dict) -> tuple[int | None, str | None]:
    """Matches the ticket description against configured keyword rules.

    TICKET_ROUTING_RULES env var, JSON: {"vpn": {"group_id": 12, "category": "Network"},
    "payroll": {"group_id": 7, "category": "HR Systems"}}. First keyword match wins;
    falls back to (None, None) so Freshservice's own default routing applies.
    """
    lowered = description.lower()
    for keyword, route in routing_rules.items():
        if keyword.lower() in lowered:
            return route.get('group_id'), route.get('category')
    return None, None
