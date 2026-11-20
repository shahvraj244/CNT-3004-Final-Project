#!/usr/bin/env python3
#analysis.py
"""Network statistics for the file-sharing server.

Standalone collector + reporter that starts with the server application
(see server.py, which records events through ``collector``).

Collected per event:
  connect / disconnect   per-client session events
  command                server-side handling time of every client command
  upload / download      transfer events with byte counts and durations
  delete                 delete events

Derived metrics:
  - upload / download data rates in MB/s (MB = 10^6 bytes)
  - file transfer times (avg / median / max)
  - system response times (average command handling time)
  - third metric: peak number of concurrently connected clients

Events are kept in memory and written to ``network_stats.json``
(override with the ``ANALYSIS_STATS_FILE`` environment variable) so the
data survives restarts and can be reviewed offline or fed into the
project report.

Extra credit: ``predict`` uses an LLM (OpenAI-compatible chat API) when the
``ANALYSIS_LLM_API_KEY`` environment variable is set; without it, a simple
bandwidth model gives a heuristic estimate. The API key is never written
to the repository and is never hardcoded.
"""

import json
import os
import re
import statistics
import sys
import threading
import time
import urllib.request

DEFAULT_STATS_FILE = 'network_stats.json'
MB = 10**6  # MB/s uses 10^6 bytes so rates are comparable to network specs


class Collector:
    """Thread-safe recorder of network events for the file-sharing server."""

    def __init__(self, stats_file=None):
        self.stats_file = stats_file or os.environ.get('ANALYSIS_STATS_FILE', DEFAULT_STATS_FILE)
        self.events = []
        self._lock = threading.Lock()

    def record(self, kind, client=None, size_bytes=0, duration_ms=0.0, success=True, details=''):
        """Append one event and rewrite the stats file so nothing is lost."""
        event = {
            'timestamp': time.time(),
            'kind': kind,
            'client': client,
            'size_bytes': int(size_bytes),
            'duration_ms': round(duration_ms, 3),
            'success': bool(success),
            'details': str(details),
        }
        with self._lock:
            self.events.append(event)
            self._save_locked()

    def _save_locked(self):
        payload = {'collected_at': time.time(), 'events': self.events}
        tmp = self.stats_file + '.tmp'
        try:
            with open(tmp, 'w') as f:
                json.dump(payload, f, indent=1)
            os.replace(tmp, self.stats_file)
        except OSError as e:
            print(f'[!] analysis: could not save stats: {e}', file=sys.stderr)

    def load(self):
        """Read previously saved events; an empty list if none exist yet."""
        if not os.path.exists(self.stats_file):
            return []
        try:
            with open(self.stats_file) as f:
                data = json.load(f)
            return data.get('events', [])
        except (OSError, ValueError) as e:
            print(f'[!] analysis: could not load stats: {e}', file=sys.stderr)
            return []


# module-level instance shared by server.py and file_commands.py
collector = Collector()


def _transfer_stats(events, kind):
    """Aggregate one transfer kind: counts, bytes, times, and MB/s rates."""
    es = [e for e in events if e['kind'] == kind]
    rates = [e['size_bytes'] / (e['duration_ms'] / 1000) / MB
             for e in es if e['duration_ms'] > 0]
    return {
        'count': len(es),
        'ok_count': sum(1 for e in es if e['success']),
        'bytes': sum(e['size_bytes'] for e in es),
        'transfer_time_ms': _summary([e['duration_ms'] for e in es]),
        'rate_mb_s_avg': round(statistics.mean(rates), 3) if rates else 0.0,
        'rate_mb_s_max': round(max(rates), 3) if rates else 0.0,
    }


def _summary(values):
    """count / avg / median / max for a list of numbers (zeros when empty)."""
    if not values:
        return {'count': 0, 'avg': 0.0, 'median': 0.0, 'max': 0.0}
    return {
        'count': len(values),
        'avg': round(statistics.mean(values), 3),
        'median': round(statistics.median(values), 3),
        'max': round(max(values), 3),
    }


def _metrics(events):
    """Aggregate raw events into the summary metric dict."""
    response_ms = [e['duration_ms'] for e in events if e['kind'] == 'command']
    # third metric: peak concurrent clients from connect/disconnect events
    active = peak = 0
    for e in sorted((e for e in events if e['kind'] in ('connect', 'disconnect')),
                    key=lambda e: e['timestamp']):
        active += 1 if e['kind'] == 'connect' else -1
        peak = max(peak, active)
    return {
        'events_total': len(events),
        'connects': sum(1 for e in events if e['kind'] == 'connect'),
        'disconnects': sum(1 for e in events if e['kind'] == 'disconnect'),
        'commands': _summary(response_ms),
        'errors': sum(1 for e in events if not e['success']),
        'uploads': _transfer_stats(events, 'upload'),
        'downloads': _transfer_stats(events, 'download'),
        'deletes': sum(1 for e in events if e['kind'] == 'delete'),
        'peak_concurrent_clients': peak,
        'bytes_moved_mb': round(sum(e['size_bytes'] for e in events
                                    if e['kind'] in ('upload', 'download')) / MB, 4),
    }


def summarize(events=None):
    """Return metric dict for the given events, defaulting to saved stats."""
    if events is None:
        events = collector.load()
    return _metrics(events)


def print_summary(stats_file=None):
    """Print a human-readable summary of the saved stats file."""
    c = Collector(stats_file) if stats_file else collector
    m = summarize(c.load())
    print(f'Network analysis summary ({c.stats_file}):')
    print(f'  events total: {m["events_total"]}')
    print(f'  connects: {m["connects"]}  disconnects: {m["disconnects"]}')
    print(f'  commands handled: {m["commands"]["count"]}'
          f'  avg response: {m["commands"]["avg"]:.3f} ms  max: {m["commands"]["max"]:.3f} ms')
    for kind in ('uploads', 'downloads'):
        t = m[kind]
        print(f'  {kind}: {t["count"]}  ok: {t["ok_count"]}  bytes: {t["bytes"]}'
              f'  rate avg: {t["rate_mb_s_avg"]:.3f} MB/s  max: {t["rate_mb_s_max"]:.3f} MB/s'
              f'  transfer avg: {t["transfer_time_ms"]["avg"]:.3f} ms')
    print(f'  deletes: {m["deletes"]}  errors: {m["errors"]}')
    print(f'  peak concurrent clients: {m["peak_concurrent_clients"]}')
    print(f'  bytes moved (upload+download): {m["bytes_moved_mb"]} MB')


class TransferTimePredictor:
    """Predict transfer time in seconds: LLM if configured, else a heuristic.

    Inputs: file size in bytes, protocol name ('tcp'/'udp'), concurrency
    (number of parallel transfers). The LLM path needs
    ``ANALYSIS_LLM_API_KEY`` (an OpenAI-compatible key); without it or on
    any failure, a simple bandwidth model is used so the extra credit menu
    option still returns an estimate.
    """

    DEFAULT_BANDWIDTH_MB_S = 100.0
    BASE_LATENCY_S = 0.02
    CONCURRENCY_FACTOR = 0.15
    PROTOCOL_BANDWIDTH = {'tcp': 100.0, 'udp': 120.0}

    def __init__(self, api_base=None, timeout=10):
        self.api_base = (api_base or os.environ.get('ANALYSIS_LLM_API_BASE')
                         or 'https://api.openai.com/v1').rstrip('/')
        self.timeout = timeout

    def heuristic(self, size_bytes, protocol='tcp', concurrency=1):
        """Simple model: transfer time = size/bandwidth + latency."""
        bw = self.PROTOCOL_BANDWIDTH.get(protocol.lower(), self.DEFAULT_BANDWIDTH_MB_S) * MB
        extra = self.BASE_LATENCY_S * (1 + self.CONCURRENCY_FACTOR * max(concurrency - 1, 0))
        return size_bytes / bw + extra

    def predict(self, size_bytes, protocol='tcp', concurrency=1):
        """Best estimate of transfer time in seconds."""
        try:
            seconds = self._predict_llm(size_bytes, protocol, concurrency)
            if seconds is not None and seconds > 0:
                return seconds
        except Exception as e:  # any LLM failure falls back to the heuristic
            print(f'[!] LLM prediction failed, using heuristic: {e}', file=sys.stderr)
        return self.heuristic(size_bytes, protocol, concurrency)

    def _predict_llm(self, size_bytes, protocol, concurrency):
        api_key = os.environ.get('ANALYSIS_LLM_API_KEY')
        if not api_key:
            return None
        prompt = (
            f'Estimate the transfer time in seconds for sending {size_bytes} bytes '
            f'over a {protocol} connection with {concurrency} concurrent transfer(s). '
            'Reply with a single number and nothing else.'
        )
        body = json.dumps({
            'model': os.environ.get('ANALYSIS_LLM_MODEL', 'gpt-4o-mini'),
            'messages': [{'role': 'user', 'content': prompt}],
            'temperature': 0,
        }).encode()
        req = urllib.request.Request(
            self.api_base + '/chat/completions',
            data=body,
            headers={'Content-Type': 'application/json',
                     'Authorization': f'Bearer {api_key}'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        content = data['choices'][0]['message']['content']
        match = re.search(r'[-+]?\d+(?:\.\d+)?', content)
        return float(match.group()) if match else None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ('--predict', 'predict'):
        if len(argv) < 4:
            print('usage: analysis.py predict <size_bytes> <protocol> <concurrency>')
            return 1
        size, protocol, concurrency = int(argv[1]), argv[2], int(argv[3])
        seconds = TransferTimePredictor().predict(size, protocol, concurrency)
        print(f'{seconds:.3f} s for {size} bytes over {protocol} '
              f'with {concurrency} concurrent transfer(s)')
        return 0
    print_summary()
    return 0


if __name__ == '__main__':
    sys.exit(main())
