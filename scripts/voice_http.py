"""Reusable private provider connections; no retries, cookies or payload logs."""
import threading
from contextlib import contextmanager

_client = None
_guard = threading.Lock()


def client():
    global _client
    with _guard:
        if _client is None:
            import httpx
            # HTTPX clients are thread safe. Credentials are supplied only per
            # request; connection reuse shares neither transcripts nor headers.
            limits = httpx.Limits(max_connections=12, max_keepalive_connections=8,
                                 keepalive_expiry=60)
            try:
                _client = httpx.Client(http2=True, trust_env=False, follow_redirects=False,
                                       limits=limits)
            except ImportError:
                _client = httpx.Client(http2=False, trust_env=False, follow_redirects=False,
                                       limits=limits)
        return _client


class Response:
    def __init__(self, response):
        self.response = response

    def close(self):
        self.response.close()

    def __iter__(self):
        # iter_lines emits complete SSE lines, including across HTTP/2 frames.
        for line in self.response.iter_lines():
            yield line.encode('utf-8') + b'\n'

    def read(self, limit):
        result = bytearray()
        for chunk in self.response.iter_bytes(chunk_size=4096):
            result.extend(chunk[:max(0, limit-len(result))])
            if len(result) >= limit:
                break
        return bytes(result)


@contextmanager
def open_request(request, timeout):
    if request.full_url not in {
            'https://openrouter.ai/api/v1/chat/completions',
            'https://openrouter.ai/api/v1/audio/transcriptions'}:
        raise ValueError('Unknown private voice provider route')
    # No automatic retries: interruption or an uncertain action cannot cause a
    # second provider submission. HTTP errors stay private at the caller layer.
    with client().stream(request.get_method(), request.full_url,
            content=request.data, headers=dict(request.header_items()), timeout=timeout) as response:
        response.raise_for_status()
        yield Response(response)
