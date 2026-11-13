"""
Operational health and readiness endpoints for VenomLink.

- /healthz/: Lightweight process liveness check (no DB dependency).
- /readyz/:  Readiness probe verifying PostgreSQL/database and cache connectivity
             without leaking tracebacks or connection credentials.
"""

import logging

from django.core.cache import cache
from django.db import connections
from django.http import JsonResponse
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


@require_GET
def healthz(request):
    """Process liveness endpoint for reverse proxies and container orchestrators."""
    return JsonResponse({'status': 'ok', 'service': 'venomlink'})


@require_GET
def readyz(request):
    """Readiness probe checking database connectivity and cache responsiveness."""
    db_status = 'ok'
    db_vendor = 'unknown'
    cache_status = 'ok'

    try:
        conn = connections['default']
        db_vendor = conn.vendor
        with conn.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:
        logger.exception('Readiness check database probe failed')
        db_status = 'error'

    try:
        probe_key = 'venomlink:readyz:probe'
        cache.set(probe_key, '1', timeout=5)
        if cache.get(probe_key) != '1':
            cache_status = 'error'
    except Exception:
        logger.exception('Readiness check cache probe failed')
        cache_status = 'error'

    healthy = db_status == 'ok' and cache_status == 'ok'
    status_code = 200 if healthy else 503
    return JsonResponse(
        {
            'status': 'ready' if healthy else 'unavailable',
            'database': db_status,
            'database_vendor': db_vendor,
            'cache': cache_status,
        },
        status=status_code,
    )
