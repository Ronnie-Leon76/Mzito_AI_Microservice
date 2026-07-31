"""Structured logging with basic secret/PII redaction.

Freshservice API keys, webhook secrets and Cliq tokens must never reach log
output, even if a caller accidentally logs a raw payload or exception string.
"""
import json
import logging
import re
import uuid
from contextvars import ContextVar

_request_id_ctx: ContextVar[str] = ContextVar('request_id', default='-')

_REDACT_PATTERNS = [
    re.compile(r'(api[_-]?key["\']?\s*[:=]\s*["\']?)([^"\'\s,}]+)', re.I),
    re.compile(r'(zapikey=)([^&\s"\']+)', re.I),
    re.compile(r'(authorization["\']?\s*[:=]\s*["\']?)(Basic|Bearer)\s+[^"\'\s,}]+', re.I),
    re.compile(r'(secret["\']?\s*[:=]\s*["\']?)([^"\'\s,}]+)', re.I),
    re.compile(r'(password["\']?\s*[:=]\s*["\']?)([^"\'\s,}]+)', re.I),
    re.compile(r'(token["\']?\s*[:=]\s*["\']?)([^"\'\s,}]+)', re.I),
]


def redact(text: str) -> str:
    redacted = text
    for pattern in _REDACT_PATTERNS:
        redacted = pattern.sub(lambda m: f'{m.group(1)}***REDACTED***', redacted)
    return redacted


def set_request_id(value: str | None = None) -> str:
    request_id = value or uuid.uuid4().hex[:16]
    _request_id_ctx.set(request_id)
    return request_id


def get_request_id() -> str:
    return _request_id_ctx.get()


class RedactingJSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = redact(record.getMessage())
        payload = {
            'level': record.levelname,
            'logger': record.name,
            'message': message,
            'request_id': get_request_id(),
        }
        if record.exc_info:
            payload['exc_info'] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


class RedactingPlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.msg = redact(str(record.msg))
        return super().format(record)


def configure_logging(level: str = 'INFO', fmt: str = 'json') -> None:
    handler = logging.StreamHandler()
    if fmt == 'json':
        handler.setFormatter(RedactingJSONFormatter())
    else:
        handler.setFormatter(RedactingPlainFormatter('%(asctime)s %(levelname)s [%(name)s] %(message)s'))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # Keep third-party libraries quieter than our own app logs.
    logging.getLogger('httpx').setLevel('WARNING')
    logging.getLogger('httpcore').setLevel('WARNING')
