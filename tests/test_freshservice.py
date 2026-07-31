import httpx
import pytest
import respx

from app.freshservice import FreshserviceClient, FreshserviceError, route_ticket


class FakeSettings:
    freshservice_domain = 'example.freshservice.com'
    freshservice_api_key = 'key'
    freshservice_workspace_id = None
    request_timeout_seconds = 5
    freshservice_max_retries = 2
    freshservice_retry_base_delay = 0.01

    @property
    def freshservice_base_url(self):
        return 'https://example.freshservice.com'


@pytest.mark.asyncio
async def test_retries_on_500_then_succeeds():
    with respx.mock:
        respx.get('https://example.freshservice.com/api/v2/tickets/1').mock(side_effect=[
            httpx.Response(500, text='boom'),
            httpx.Response(200, json={'ticket': {'id': 1, 'subject': 'ok'}}),
        ])
        client = FreshserviceClient(FakeSettings())
        result = await client.get_ticket(1)
        assert result['subject'] == 'ok'
        await client.close()


@pytest.mark.asyncio
async def test_does_not_retry_on_400():
    with respx.mock:
        respx.get('https://example.freshservice.com/api/v2/tickets/1').mock(
            return_value=httpx.Response(400, text='bad request')
        )
        client = FreshserviceClient(FakeSettings())
        with pytest.raises(FreshserviceError):
            await client.get_ticket(1)
        await client.close()


def test_route_ticket_matches_keyword():
    rules = {'vpn': {'group_id': 12, 'category': 'Network'}}
    group_id, category = route_ticket('My VPN keeps disconnecting', rules)
    assert group_id == 12
    assert category == 'Network'


def test_route_ticket_no_match_returns_none():
    group_id, category = route_ticket('printer is jammed', {'vpn': {'group_id': 12}})
    assert group_id is None
    assert category is None
