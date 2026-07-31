import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .cliq import parse_cliq_payload
from .config import Settings, get_settings
from .freshservice import FreshserviceClient
from .logging_utils import configure_logging, set_request_id
from .rate_limit import InMemoryRateLimiter, RateLimitExceeded
from .security import client_identity, verify_shared_secret, verify_signature
from .service import HelpdeskService
from .session_store import build_session_store

logger = logging.getLogger('cliq-freshservice')


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    app.state.settings = settings
    app.state.freshservice = FreshserviceClient(settings)
    app.state.sessions = build_session_store(settings)
    app.state.rate_limiter = InMemoryRateLimiter(settings.rate_limit_requests, settings.rate_limit_window_seconds)
    logger.info('Startup complete env=%s', settings.app_env)
    yield
    await app.state.freshservice.close()
    logger.info('Shutdown complete')


app = FastAPI(title='Zoho Cliq to Mzito AI Middleware', version='2.0.0', lifespan=lifespan)


@app.middleware('http')
async def request_context_middleware(request: Request, call_next):
    request_id = set_request_id(request.headers.get('X-Request-Id'))
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1000
    response.headers['X-Request-Id'] = request_id
    logger.info('%s %s -> %s (%.1fms)', request.method, request.url.path, response.status_code, duration_ms)
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={'detail': exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception('Unhandled error on %s', request.url.path)
    return JSONResponse(status_code=500, content={'detail': 'Internal error'})


@app.get('/health')
async def health() -> dict:
    return {'status': 'ok'}


@app.get('/ready')
async def ready(request: Request) -> dict:
    """Confirms Freshservice credentials actually work, not just that the
    process is up -- useful as a Kubernetes/App Service readiness probe so a
    bad API key fails deploys instead of failing silently on the first user."""
    try:
        await request.app.state.freshservice.search_solutions('healthcheck', limit=1)
    except Exception as exc:  # noqa: BLE001 - readiness probe must not crash the app
        raise HTTPException(status_code=503, detail=f'Freshservice not reachable: {exc}') from exc
    return {'status': 'ready'}


@app.post('/webhooks/cliq')
async def cliq_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
    x_webhook_secret: str | None = Header(default=None),
    x_signature: str | None = Header(default=None),
) -> JSONResponse:
    body = await request.body()
    verify_shared_secret(settings, x_webhook_secret)
    verify_signature(settings, body, x_signature)

    payload = await request.json()
    incoming = parse_cliq_payload(payload)
    logger.info('Cliq message received chat_id=%s text_len=%s', incoming.chat_id, len(incoming.text))

    identity = client_identity(incoming.user.id, incoming.user.email, request.client.host if request.client else 'unknown')
    if settings.rate_limit_enabled:
        try:
            request.app.state.rate_limiter.check(identity)
        except RateLimitExceeded as exc:
            logger.warning('Rate limit hit for %s', identity)
            return JSONResponse(
                {'text': 'You are sending messages a bit too fast -- please wait a few seconds and try again.'},
                headers={'Retry-After': str(int(exc.retry_after))},
            )

    service = HelpdeskService(settings, request.app.state.freshservice, request.app.state.sessions)
    response = await service.handle(incoming)
    return JSONResponse(response)
