import re
import json
from urllib import request as urlrequest
from urllib.error import URLError, HTTPError

import openai
from django.conf import settings


ABUSIVE_CATEGORY_KEYS = {
    'harassment',
    'harassment/threatening',
    'hate',
    'hate/threatening',
    'sexual',
    'sexual/minors',
    'violence',
    'violence/graphic',
}

ABUSIVE_SCORE_THRESHOLD = float(getattr(settings, 'ABUSIVE_MODERATION_SCORE_THRESHOLD', 0.75))

# Fallback profanity guard for local/dev environments where API key may be missing.
LOCAL_PROFANITY_PATTERNS = tuple(
    getattr(
        settings,
        'LOCAL_PROFANITY_PATTERNS',
        [
            r'\bf+u+c+k+(?:e?r|i+n+g+)?\b',
            r'\bshit+(?:ty)?\b',
            r'\bbitch(?:es)?\b',
            r'\basshole\b',
            r'\bmotherf+u+c+k+e?r?\b',
            r'\bcunt\b',
            r'\bdick\b',
            r'\bbastard\b',
            r'\bbomb(?:s|ing|ed)?\b',
            r'\bexplosive(?:s)?\b',
            r'\bgrenade(?:s)?\b',
            r'\bdetonat(?:e|ed|ing|or)\b',
            r'\bkill\s+yourself\b',
            r'\bkys\b',
            r'\bi\s*(?:will|am\s+gonna|gonna|want\s+to)?\s*kill\s+you\b',
            r'\brap(?:e|ed|ing)\b',
        ],
    )
)
_LOCAL_PROFANITY_RE = [re.compile(pattern, re.IGNORECASE) for pattern in LOCAL_PROFANITY_PATTERNS]


def _category_key_variants(key):
    return {
        key,
        key.replace('/', '_'),
        key.replace('/', '.'),
    }


def _obj_to_dict(value):
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, 'model_dump'):
        try:
            dumped = value.model_dump()
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass
    data = getattr(value, '__dict__', None)
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if not str(k).startswith('_')}
    return {}


def _is_abusive_result(result):
    if isinstance(result, dict):
        categories = _obj_to_dict(result.get('categories'))
        scores = _obj_to_dict(result.get('category_scores'))
        flagged = bool(result.get('flagged', False))
    else:
        categories = _obj_to_dict(getattr(result, 'categories', None))
        scores = _obj_to_dict(getattr(result, 'category_scores', None))
        flagged = bool(getattr(result, 'flagged', False))
    if not flagged:
        return False

    abusive_hit = False
    max_abusive_score = 0.0

    for key in ABUSIVE_CATEGORY_KEYS:
        for variant in _category_key_variants(key):
            if bool(categories.get(variant, False)):
                abusive_hit = True
            try:
                score = float(scores.get(variant, 0.0))
                if score > max_abusive_score:
                    max_abusive_score = score
            except (TypeError, ValueError):
                continue

    if not abusive_hit:
        return False

    # Block only when abusive category confidence is strong.
    if max_abusive_score >= ABUSIVE_SCORE_THRESHOLD:
        return True

    return False


def _contains_local_profanity(text):
    if not text:
        return False
    for pattern in _LOCAL_PROFANITY_RE:
        if pattern.search(text):
            return True
    return False


def _call_openai_moderation_http(api_key, text):
    """SDK-independent moderation call to avoid client dependency conflicts."""
    payload = json.dumps({'model': 'omni-moderation-latest', 'input': text}).encode('utf-8')
    req = urlrequest.Request(
        url='https://api.openai.com/v1/moderations',
        data=payload,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    with urlrequest.urlopen(req, timeout=20) as resp:
        body = resp.read().decode('utf-8')
    data = json.loads(body or '{}')
    results = data.get('results') or []
    return results[0] if results else {}

def check_content_moderation(text):
    """
    Check if content violates OpenAI's moderation policies.
    Returns True if content is flagged as abusive, False otherwise.
    """
    value = str(text or '').strip()
    if not value:
        return False

    if not getattr(settings, 'ENABLE_CONTENT_MODERATION', True):
        # Moderation can be disabled explicitly in settings when needed.
        return False

    # Always run quick local guard first.
    if _contains_local_profanity(value):
        return True

    fail_closed = bool(getattr(settings, 'MODERATION_FAIL_CLOSED', False))

    if not settings.OPENAI_API_KEY:
        # No API key: rely on local guard and optional fail-closed mode.
        return fail_closed

    try:
        client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.moderations.create(input=value)
        result = response.results[0]
        return _is_abusive_result(result)
    except Exception as e:
        print(f"OpenAI SDK moderation error: {e}")
        # Fallback to direct HTTPS call when SDK/http client versions conflict.
        try:
            result = _call_openai_moderation_http(settings.OPENAI_API_KEY, value)
            return _is_abusive_result(result)
        except HTTPError as http_exc:
            print(f"OpenAI HTTP moderation error: {http_exc}")
            # If API is temporarily rate-limited, fall back to local checks.
            if getattr(http_exc, 'code', None) == 429:
                return _contains_local_profanity(value)
            return fail_closed
        except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as http_exc:
            print(f"OpenAI HTTP moderation error: {http_exc}")
            return fail_closed