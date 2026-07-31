import hashlib
import hmac

from fastapi import HTTPException, status

from .config import Settings


def verify_shared_secret(settings: Settings, supplied: str | None) -> None:
    """Primary auth: a long random value the Deluge Message Handler sends as
    X-Webhook-Secret on every invokeUrl call. This is the only auth Cliq's
    invokeUrl task gives you out of the box (it can set arbitrary headers),
    so treat this secret as the real credential and rotate it if it ever
    leaks into a log or a shared script.
    """
    if not supplied or not hmac.compare_digest(supplied, settings.webhook_shared_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid webhook secret')


def verify_signature(settings: Settings, body: bytes, signature: str | None) -> None:
    """Optional second factor if you also front this with a Cliq Extension
    (rather than a plain bot Message Handler). Extensions sign requests with
    a Cliq-managed keypair and send X-Cliq-Signature -- set CLIQ_SIGNING_KEY
    to that extension's public key (PEM) to enforce it. Skipped when the
    header is absent, e.g. for a plain Message Handler integration.
    """
    if not signature:
        return
    if not settings.cliq_signing_key:
        raise HTTPException(status_code=401, detail='X-Cliq-Signature present but CLIQ_SIGNING_KEY is not configured')
    expected = hmac.new(settings.cliq_signing_key.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.removeprefix('sha256='), expected):
        raise HTTPException(status_code=401, detail='Invalid webhook signature')


def client_identity(user_id: str | None, user_email: str | None, fallback_ip: str) -> str:
    """Rate-limit / logging identity key: prefer the Cliq user, fall back to
    the caller's IP (useful in development / before Cliq passes a user)."""
    return user_id or user_email or f'ip:{fallback_ip}'
