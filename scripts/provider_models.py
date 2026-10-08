"""Live auto-sensing of AI models directly from connected API providers (OpenRouter, OpenAI, etc.).

Models are fetched dynamically from the provider API source rather than relying on static presets.
"""
import json
import os
import re
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional

_CACHE_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {
    'models': [],
    'model_details': [],
    'vendors': [],
    'providers': [],
    'timestamp': 0,
}
_CACHE_TTL = 900  # 15 minutes


def _fetch_openrouter_models(api_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch live models catalog directly from OpenRouter API source."""
    headers = {
        'User-Agent': 'Central-AI-OS/1.0',
        'Accept': 'application/json',
    }
    if api_key and api_key.strip():
        headers['Authorization'] = f'Bearer {api_key.strip()}'

    req = urllib.request.Request(
        'https://openrouter.ai/api/v1/models',
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        if resp.status != 200:
            return []
        raw = json.loads(resp.read().decode('utf-8'))
        return raw.get('data', [])


def _parse_model_entry(m: Dict[str, Any]) -> Dict[str, Any]:
    """Parse raw provider model object into a structured Central AI model record."""
    model_id = str(m.get('id', '')).strip()
    name = str(m.get('name') or model_id).strip()
    context_length = m.get('context_length') or m.get('top_provider', {}).get('context_length') or 0
    description = str(m.get('description') or '')[:300].strip()
    
    # Extract vendor prefix (e.g. 'anthropic' from 'anthropic/claude-3.7-sonnet')
    vendor = 'other'
    if '/' in model_id:
        vendor = model_id.split('/', 1)[0].lower()
    
    params = m.get('supported_parameters') or []
    is_free = ':free' in model_id or m.get('pricing', {}).get('prompt') == '0'

    return {
        'id': model_id,
        'name': name,
        'vendor': vendor,
        'context_length': context_length,
        'description': description,
        'tools': 'tools' in params,
        'reasoning': 'reasoning' in params or 'include_reasoning' in params or bool(m.get('reasoning')),
        'is_free': is_free,
        'provider': 'openrouter',
    }


def get_catalog_models(settings: Dict[str, Any], force_refresh: bool = False) -> Dict[str, Any]:
    """Auto-sense models from the connected API provider and return catalog with details."""
    global _CACHE
    now = time.monotonic()

    with _CACHE_LOCK:
        if not force_refresh and _CACHE['models'] and (now - _CACHE['timestamp'] < _CACHE_TTL):
            return dict(_CACHE)

    openrouter_key = (
        settings.get('OPENROUTER_API_KEY')
        or os.environ.get('OPENROUTER_API_KEY', '')
    ).strip()

    raw_models: List[Dict[str, Any]] = []
    try:
        raw_models = _fetch_openrouter_models(openrouter_key)
    except Exception as e:
        print(f"[provider_models] Failed to fetch live models from OpenRouter: {e}")

    parsed_details: List[Dict[str, Any]] = []
    seen_ids = set()

    # Prioritized vendor order for clean grouping & display
    PRIORITY_VENDORS = [
        'nousresearch',
        'anthropic',
        'openai',
        'google',
        'deepseek',
        'meta-llama',
        'qwen',
        'mistralai',
        'cohere',
        'x-ai',
    ]

    for item in raw_models:
        mid = item.get('id')
        if not mid or mid in seen_ids:
            continue
        seen_ids.add(mid)
        parsed = _parse_model_entry(item)
        parsed_details.append(parsed)

    # If live fetch succeeded, sort models thoughtfully:
    # 1. Non-free models before test/free models
    # 2. Priority vendors first
    # 3. Alphabetical by name
    if parsed_details:
        def sort_key(d: Dict[str, Any]):
            vendor = d['vendor']
            v_rank = PRIORITY_VENDORS.index(vendor) if vendor in PRIORITY_VENDORS else 99
            free_rank = 1 if d['is_free'] else 0
            return (free_rank, v_rank, vendor, d['name'].lower())

        parsed_details.sort(key=sort_key)
    else:
        # Fallback if offline or network unavailable
        fallback_ids = [
            'nousresearch/hermes-4-405b',
            'anthropic/claude-3-7-sonnet',
            'anthropic/claude-3-5-sonnet',
            'openai/gpt-4o',
            'openai/gpt-4o-mini',
            'openai/o3-mini',
            'deepseek/deepseek-chat-v3.1',
            'deepseek/deepseek-v4.1-flash',
            'meta-llama/llama-3.3-70b-instruct',
            'google/gemini-2.5-pro',
            'google/gemini-2.5-flash',
        ]
        for fid in fallback_ids:
            parsed_details.append({
                'id': fid,
                'name': fid.split('/')[-1].replace('-', ' ').title(),
                'vendor': fid.split('/')[0] if '/' in fid else 'other',
                'context_length': 128000,
                'description': '',
                'tools': True,
                'reasoning': True,
                'is_free': False,
                'provider': 'openrouter',
            })

    all_model_ids = [d['id'] for d in parsed_details]

    # Collect available vendors
    vendors_set = set()
    for d in parsed_details:
        if d['vendor'] and d['vendor'] != 'other':
            vendors_set.add(d['vendor'])
    sorted_vendors = [v for v in PRIORITY_VENDORS if v in vendors_set] + sorted(
        [v for v in vendors_set if v not in PRIORITY_VENDORS]
    )

    # Structure into providers list for Hermes options compatibility
    providers_list = [
        {
            'slug': 'openrouter',
            'name': 'OpenRouter (Live Catalog)',
            'models': all_model_ids,
            'count': len(all_model_ids),
        }
    ]

    result = {
        'models': all_model_ids,
        'model_details': parsed_details,
        'vendors': sorted_vendors,
        'providers': providers_list,
        'total': len(all_model_ids),
        'timestamp': now,
        'source': 'api_provider',
    }

    with _CACHE_LOCK:
        _CACHE = result

    return result
