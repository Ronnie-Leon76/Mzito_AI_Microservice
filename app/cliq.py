from typing import Any

from .models import CliqIncomingMessage, CliqUser, KBArticle


def parse_cliq_payload(payload: dict[str, Any]) -> CliqIncomingMessage:
    """Accepts common Cliq Message Handler payload variations.

    The Deluge Message Handler in deluge/message_handler.dg forwards the
    handler's own `message`, `user` and `chat` context maps verbatim as the
    JSON body of the invokeUrl call, so this mirrors that shape. Keep the raw
    payload logged in development and adjust selectors here if you change
    what the Deluge script forwards.
    """
    message = payload.get('message') or {}
    sender = payload.get('user') or payload.get('sender') or message.get('sender') or {}
    text = (
        payload.get('text')
        or message.get('text')
        or payload.get('message_text')
        or (payload.get('command') or {}).get('arguments')
        or ''
    )
    email = sender.get('email') or sender.get('email_id')
    chat = payload.get('chat') or {}
    return CliqIncomingMessage(
        text=str(text).strip(),
        user=CliqUser(
            id=str(sender.get('id') or sender.get('user_id') or '') or None,
            name=sender.get('name') or sender.get('first_name') or 'User',
            email=email,
        ),
        chat_id=str(payload.get('chat_id') or chat.get('id') or chat.get('chat_id') or '') or None,
        raw=payload,
    )


def session_key(message: CliqIncomingMessage) -> str:
    """Stable per-conversation key for multi-turn state.

    Prefers chat_id (works for both 1:1 and channel contexts); falls back to
    the user id/email so single-user testing still works without a chat_id.
    """
    identity = message.user.id or message.user.email or message.user.name
    return f'{message.chat_id or "dm"}:{identity}'


def text_response(text: str) -> dict:
    return {'text': text}


def card_response(text: str, title: str, buttons: list[dict]) -> dict:
    """Cliq message-card response with action buttons.

    `buttons` items look like: {"label": "View article", "url": "https://..."}
    Rendered as open.url buttons -- no Cliq-side function wiring required, so
    this works purely from the webhook response with no extra Deluge code.
    """
    return {
        'text': text,
        'card': {'title': title, 'theme': 'modern-inline'},
        'buttons': [
            {
                'label': b['label'][:30],
                'type': '+',
                'action': {'type': 'open.url', 'data': {'web': b['url']}},
            }
            for b in buttons
            if b.get('url')
        ],
    }


def kb_response(query: str, articles: list[KBArticle]) -> dict:
    if not articles:
        return text_response(
            f'I could not find a relevant knowledge article for \u201c{query}\u201d.\n\n'
            'Reply with: create ticket: <brief description of the issue>'
        )
    lines = [f'I found {len(articles)} relevant knowledge article(s):']
    for index, article in enumerate(articles, start=1):
        summary = f' \u2014 {article.description[:180]}' if article.description else ''
        lines.append(f'{index}. {article.title}{summary}')
    lines.append('\nReply with \u201ccreate ticket: <description>\u201d if the issue remains unresolved.')

    buttons = [{'label': a.title[:30], 'url': a.url} for a in articles if a.url]
    if buttons:
        return card_response('\n\n'.join(lines), title='Knowledge base results', buttons=buttons)
    return text_response('\n\n'.join(lines))
