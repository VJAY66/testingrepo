"""
Elasticsearch search backend with graceful fallback to DB search.
Enable by setting ELASTICSEARCH_URL env var.
"""
from django.conf import settings
from django.db.models import Q


def _get_es_client():
    url = getattr(settings, 'ELASTICSEARCH_URL', '')
    if not url:
        return None
    try:
        from elasticsearch import Elasticsearch
        return Elasticsearch(url, request_timeout=5)
    except ImportError:
        return None


def search_posts(query, category=None, limit=20):
    """Search posts using Elasticsearch if available, else Django ORM."""
    from discussions.models import Post
    es = _get_es_client()
    if es:
        try:
            body = {
                'query': {
                    'bool': {
                        'must': [{'multi_match': {'query': query, 'fields': ['title^3', 'content', 'hashtags']}}],
                        'filter': [{'term': {'is_draft': False}}, {'term': {'is_deleted_by_moderation': False}}],
                    }
                },
                'size': limit,
            }
            if category:
                body['query']['bool']['filter'].append({'term': {'category': category}})
            resp = es.search(index=f"{settings.ELASTICSEARCH_INDEX_PREFIX}_posts", body=body)
            ids = [hit['_id'] for hit in resp['hits']['hits']]
            posts = {p.id: p for p in Post.objects.filter(id__in=ids)}
            return [posts[pid] for pid in ids if pid in posts]
        except Exception:
            pass
    # Fallback to ORM
    qs = Post.objects.filter(
        Q(title__icontains=query) | Q(content__icontains=query) | Q(hashtags__icontains=query),
        is_draft=False, is_deleted_by_moderation=False,
    )
    if category:
        qs = qs.filter(category=category)
    return list(qs[:limit])
