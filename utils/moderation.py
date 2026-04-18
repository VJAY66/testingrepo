import openai
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

def check_content_moderation(text):
    """
    Check if content violates OpenAI's moderation policies.
    Returns True if content is flagged as abusive, False otherwise.
    """
    if not settings.OPENAI_API_KEY:
        # If no API key, allow content (fail open for development)
        return False

    try:
        client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.moderations.create(input=text)
        result = response.results[0]
        return result.flagged
    except Exception as e:
        # Log the error but fail open to avoid blocking legitimate content
        print(f"OpenAI moderation error: {e}")
        return False