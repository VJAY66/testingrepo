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

# Comprehensive word-level blocklist — checked after normalization so evasion
# tricks like f**k, @ss, sh!t, fuuuck are also caught.
BAD_WORD_SET = frozenset({
    # Common profanity
    'fuck', 'fucker', 'fuckers', 'fucking', 'fucked', 'fucks', 'fuckhead', 'fuckface',
    'motherfucker', 'motherfuckers', 'motherfucking',
    'shit', 'shits', 'shitty', 'shitting', 'bullshit', 'horseshit', 'shitheads', 'shithead',
    'bitch', 'bitches', 'bitching', 'bitchy', 'bitchass',
    'asshole', 'assholes', 'ass', 'asses', 'jackass', 'dumbass', 'smartass',
    'cunt', 'cunts',
    'dick', 'dicks', 'dickhead', 'dickheads', 'dickface',
    'bastard', 'bastards',
    'piss', 'pisses', 'pissed', 'pissing', 'pissoff',
    'cock', 'cocks', 'cockhead', 'cocksucking',
    'pussy', 'pussies',
    'wank', 'wanker', 'wankers', 'wanking', 'wanked',
    'twat', 'twats',
    'arse', 'arses', 'arshole', 'arsehole', 'arseholes',
    'bollocks',
    'prick', 'pricks',
    'tits', 'titties', 'boobs',
    'whore', 'whores', 'whorebag',
    'slut', 'sluts', 'slutty',
    'crap', 'crappy',
    'dumbfuck', 'dumbfucker',
    # Racial / ethnic slurs
    'nigger', 'niggers', 'nigga', 'niggas',
    'kike', 'kikes',
    'chink', 'chinks',
    'spic', 'spics',
    'wetback', 'wetbacks',
    'gook', 'gooks',
    'cracker', 'crackers',
    'honky',
    'beaner', 'beaners',
    'towelhead', 'towelheads',
    'raghead', 'ragheads',
    'zipperhead',
    'coon', 'coons',
    'darkie',
    'sandnigger',
    'camel jockey',
    # Sexual orientation / gender slurs
    'faggot', 'faggots', 'fag', 'fags',
    'dyke', 'dykes',
    'tranny', 'trannies',
    'homo', 'homos',
    'queer',  # slur context — OpenAI API catches nuance better
    # Disability slurs
    'retard', 'retarded', 'retards',
    'spastic', 'spaz',
    'moron', 'morons',
    'imbecile',
    # Explicit sexual violence
    'rape', 'raping', 'rapist', 'raped', 'rapists',
    'pedophile', 'pedophiles', 'paedophile', 'paedophiles',
    'molest', 'molested', 'molester', 'molesters', 'molestation',
})

# Regex patterns for multi-word phrases, contextual threats, and censored forms
# like f**k / f*ck where symbols replace middle letters entirely.
_PHRASE_PATTERNS = [
    # Threats / violence
    r'\bkill\s+yourself\b',
    r'\bkys\b',
    r'\bi\s*(?:will|am\s+gonna|gonna|want\s+to)?\s*kill\s+you\b',
    r'\bi\s*(?:will|am\s+gonna|gonna|want\s+to)?\s*(?:hurt|harm|attack|rape)\s+you\b',
    r'\bbomb(?:s|ing|ed)?\b',
    r'\bexplosive(?:s)?\b',
    r'\bgrenade(?:s)?\b',
    r'\bdetonat(?:e|ed|ing|or)\b',
    # Multi-word slurs
    r'\bcamel\s*jockey\b',
    r'\bsand\s*nigger\b',
    # Censored forms where symbols replace middle letters (f**k, sh*t, b**ch).
    # u? and c? are optional so the pattern matches even when those letters are
    # replaced by asterisks or dashes (e.g. f--k, f**k).
    r'\bf[\W]*u?[\W]*c?[\W]*k\b',
    r'\bf[\W]*u?[\W]*c?[\W]*k?[\W]*i[\W]*n[\W]*g\b',  # f***ing / fucking
    r'\bsh[\W]*[i!1]?[\W]*t(?:ty|s|ter|head)?\b',
    r'\bb[\W]*[i!1]?[\W]*t?[\W]*c[\W]*h(?:es|ing|y)?\b',  # bitch, b**ch
    r'\bc[\W]*u[\W]*n[\W]*t[s]?\b',
    r'\ba[\W]*s[\W]*s(?:h[o0]le|hole|es)?\b',
    r'\bd[\W]*[i!1][\W]*c[\W]*k(?:head|s|face)?\b',
    r'\bm[\W]*[o0][\W]*t[\W]*h[\W]*e[\W]*r[\W]*f[\W]*u?[\W]*c?[\W]*k\b',
]

# Override phrase list from settings if provided.
_PHRASE_PATTERNS = list(getattr(settings, 'LOCAL_PROFANITY_PATTERNS', _PHRASE_PATTERNS))
_PHRASE_RE = [re.compile(p, re.IGNORECASE) for p in _PHRASE_PATTERNS]

# Map common symbol/leet substitutions to their alpha equivalents before checking.
_LEET_TABLE = str.maketrans({
    '@': 'a',
    '4': 'a',
    '$': 's',
    '5': 's',
    '3': 'e',
    '0': 'o',
    '1': 'i',
    '!': 'i',
    '+': 't',
    '7': 't',
    '9': 'g',
})


def _normalize_word(word):
    """Apply leet substitutions and strip non-alpha chars (@ss → ass, sh!t → shit)."""
    word = word.lower().translate(_LEET_TABLE)
    return re.sub(r'[^a-z]', '', word)


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

    if max_abusive_score >= ABUSIVE_SCORE_THRESHOLD:
        return True

    return False


def _contains_local_profanity(text):
    if not text:
        return False

    # Phase 1: multi-word phrase and censored-form regex patterns
    for pattern in _PHRASE_RE:
        if pattern.search(text):
            return True

    # Phase 2: word-level check with normalization.
    # We check the direct normalized form first, then a repeat-collapsed form so
    # that elongated words like "fuuuuck" still match "fuck" in the set.
    for word in text.split():
        normalized = _normalize_word(word)
        if normalized in BAD_WORD_SET:
            return True
        collapsed = re.sub(r'(.)\1+', r'\1', normalized)
        if collapsed != normalized and collapsed in BAD_WORD_SET:
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
    Returns True if content contains abusive language, False otherwise.
    Always runs the local filter first; falls back to OpenAI API when available.
    """
    value = str(text or '').strip()
    if not value:
        return False

    if not getattr(settings, 'ENABLE_CONTENT_MODERATION', True):
        return False

    # Local guard runs first — catches bad words even without an API key.
    if _contains_local_profanity(value):
        return True

    fail_closed = bool(getattr(settings, 'MODERATION_FAIL_CLOSED', False))

    if not settings.OPENAI_API_KEY:
        return fail_closed

    try:
        client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.moderations.create(input=value)
        result = response.results[0]
        return _is_abusive_result(result)
    except Exception as e:
        print(f"OpenAI SDK moderation error: {e}")
        try:
            result = _call_openai_moderation_http(settings.OPENAI_API_KEY, value)
            return _is_abusive_result(result)
        except HTTPError as http_exc:
            print(f"OpenAI HTTP moderation error: {http_exc}")
            if getattr(http_exc, 'code', None) == 429:
                return _contains_local_profanity(value)
            return fail_closed
        except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as http_exc:
            print(f"OpenAI HTTP moderation error: {http_exc}")
            return fail_closed
