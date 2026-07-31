"""Outbound calls back to Cliq, using the Bot Message API.

Not needed for the synchronous webhook reply (the FastAPI response body IS
the bot's reply for that turn). This is for cases where work outlives the
Deluge invokeUrl timeout (Cliq's invokeUrl caps out around 40s) -- e.g. you
kick off a slow Freshservice operation, reply immediately with "Working on
it...", then post the real result here once it's ready.

Requires CLIQ_BOT_UNIQUE_NAME and CLIQ_WEBHOOK_TOKEN (Settings -> Integrations
-> Webhook Tokens).
"""
import logging

import httpx

from .config import Settings

logger = logging.getLogger('cliq-freshservice.cliq_client')


class CliqClientError(RuntimeError):
    pass


class CliqClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.client = httpx.AsyncClient(base_url=settings.cliq_api_base_url, timeout=10, transport=transport)

    async def close(self) -> None:
        await self.client.aclose()

    @property
    def configured(self) -> bool:
        return bool(self.settings.cliq_bot_unique_name and self.settings.cliq_webhook_token)

    async def post_to_bot_chat(self, text: str) -> None:
        """Posts a message into the bot's own chat via the bot message endpoint.

        For posting into an arbitrary user's 1:1 chat you instead need the
        'Post to user' capability, which Cliq only exposes from inside Deluge
        (zoho.cliq.postToUser) or via an OAuth-scoped REST call -- the simple
        webhook token below only targets the bot's own conversation/channel.
        """
        if not self.configured:
            raise CliqClientError('Cliq outbound client is not configured (bot name / webhook token missing)')
        response = await self.client.post(
            f'/bots/{self.settings.cliq_bot_unique_name}/message',
            params={'zapikey': self.settings.cliq_webhook_token},
            json={'text': text},
        )
        if response.status_code >= 400:
            raise CliqClientError(f'Cliq returned {response.status_code}: {response.text[:300]}')
