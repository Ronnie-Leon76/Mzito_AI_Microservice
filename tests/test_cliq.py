from app.cliq import card_response, kb_response, parse_cliq_payload, session_key
from app.models import CliqIncomingMessage, CliqUser, KBArticle


def test_parse_payload():
    parsed = parse_cliq_payload({
        'message': {'text': 'Reset MFA'},
        'user': {'id': '123', 'name': 'Loise', 'email': 'loise@example.com'},
        'chat': {'id': 'chat-1'},
    })
    assert parsed.text == 'Reset MFA'
    assert parsed.user.email == 'loise@example.com'
    assert parsed.chat_id == 'chat-1'


def test_session_key_prefers_chat_and_user_id():
    message = CliqIncomingMessage(text='hi', user=CliqUser(id='u1', name='Loise'), chat_id='c1')
    assert session_key(message) == 'c1:u1'


def test_session_key_falls_back_without_chat_id():
    message = CliqIncomingMessage(text='hi', user=CliqUser(email='loise@example.com'))
    assert session_key(message) == 'dm:loise@example.com'


def test_kb_response_includes_buttons_when_urls_present():
    articles = [KBArticle(id=1, title='Reset MFA', description='How to reset MFA', url='https://x/1')]
    result = kb_response('mfa', articles)
    assert 'card' in result
    assert result['buttons'][0]['action']['data']['web'] == 'https://x/1'


def test_kb_response_falls_back_to_text_without_urls():
    articles = [KBArticle(id=1, title='Reset MFA', description='', url=None)]
    result = kb_response('mfa', articles)
    assert 'card' not in result


def test_card_response_truncates_long_labels():
    result = card_response('body', 'title', [{'label': 'x' * 50, 'url': 'https://example.com'}])
    assert len(result['buttons'][0]['label']) == 30
