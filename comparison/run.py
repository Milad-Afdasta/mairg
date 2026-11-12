"""Whole-application POC comparison; exact functional checks precede timings."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import platform
import resource
import selectors
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(backend, source, state):
    if backend == 'mairg':
        from mairg.compiler import compile_project
        project = compile_project(source)
        return project, lambda name, args: project.call(name, args, state_dir=state)
    from comparison.reference_inventory import Inventory
    app = Inventory(source, state)
    return app, app.call


def child(args):
    app, call = load(args.backend, Path(args.source), Path(args.state))
    if args.invoke:
        result = call('list_items', [])
        print(json.dumps({'result': result, 'peak_rss': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
        return
    if args.backend == 'mairg':
        from mairg.web import make_wsgi_app
        application = make_wsgi_app(app, state_dir=args.state)
    else:
        application = app.wsgi()
    from waitress import create_server
    server = create_server(application, host='127.0.0.1', port=0, threads=4,
                           max_request_body_size=1024 * 1024, expose_tracebacks=False)

    def stats():
        for line in sys.stdin:
            if line.strip() == 'stats':
                print(json.dumps({'peak_rss': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}), flush=True)

    threading.Thread(target=stats, daemon=True).start()
    print(json.dumps({'ready': int(server.effective_port),
                      'peak_rss': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}), flush=True)
    try:
        server.asyncore.loop(timeout=server.adj.asyncore_loop_timeout, map=server._map,
                             use_poll=server.adj.asyncore_use_poll)
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        server.task_dispatcher.shutdown(timeout=2)


class Service:
    def __init__(self, backend, source, state):
        self.command = [sys.executable, str(Path(__file__).resolve()), '--child', '--backend', backend,
                        '--source', str(source), '--state', str(state)]
        started = time.perf_counter()
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True)
        try:
            info = self.line()
        except BaseException:
            self.close()
            raise
        self.ready_seconds = time.perf_counter() - started
        self.ready_rss = info['peak_rss']
        self.url = f"http://127.0.0.1:{info['ready']}"

    def line(self):
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=10):
                raise TimeoutError('owned benchmark process did not respond within 10 seconds')
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError('owned benchmark process exited before readiness/result')
        return json.loads(line)

    def stats(self):
        self.process.stdin.write('stats\n')
        self.process.stdin.flush()
        return self.line()

    def close(self):
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream:
                stream.close()


def request(service, method, path, body=None):
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(service.url + path, data=payload, method=method,
                                 headers={'Content-Type': 'application/json'})
    started = time.perf_counter_ns()
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as response:
        status, raw = response.code, response.read()
    result = json.loads(raw)
    elapsed_ms = (time.perf_counter_ns() - started) / 1e6
    return (status, result), elapsed_ms


def distribution(samples):
    ordered = sorted(samples)
    return {'samples_ms': samples, 'median_ms': statistics.median(samples),
            'p95_ms': ordered[min(len(ordered) - 1, int(len(ordered) * .95))],
            'min_ms': min(samples), 'max_ms': max(samples)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child', action='store_true')
    parser.add_argument('--invoke', action='store_true')
    parser.add_argument('--backend', choices=('mairg', 'python'))
    parser.add_argument('--source', default=str(ROOT / 'examples/inventory'))
    parser.add_argument('--state')
    parser.add_argument('--requests', type=int, default=60)
    parser.add_argument('--output', default='/tmp/mairg-poc-comparison.json')
    args = parser.parse_args(argv)
    if args.child or args.invoke:
        child(args)
        return 0
    if args.requests < 10:
        parser.error('at least 10 timed requests are required')
    source = Path(args.source).resolve()
    report = {'scope': 'practical application POC, not a language-level speed claim',
              'metadata': {'python': sys.version, 'platform': platform.platform(),
                           'sqlite': __import__('sqlite3').sqlite_version,
                           'server': 'Waitress 3.0.2, four threads, loopback HTTP',
                           'command': [sys.executable, *sys.argv],
                           'rss_unit': 'bytes' if sys.platform == 'darwin' else 'KiB',
                           'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                             for p in [source/'program.json', source/'schema.json', Path(__file__),
                                                       ROOT/'comparison/reference_inventory.py']},
                           'git_commit': subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True,
                                                         text=True, check=True).stdout.strip()},
              'correctness': {}, 'backends': {}}
    services = {}
    with tempfile.TemporaryDirectory(prefix='mairg-poc-compare-') as temporary:
        base = Path(temporary)
        calls = {}
        try:
            for backend in ('mairg', 'python'):
                state = base / backend
                _, calls[backend] = load(backend, source, state)
                assert calls[backend]('init', []) == {'ready': True, 'version': 1}
                services[backend] = Service(backend, source, state)
            sequence = [
                ('GET', '/items', None),
                ('POST', '/items', {'name': "O'Reilly café", 'stock': 10}),
                ('POST', '/items', {'name': ' ', 'stock': 5}),
                ('POST', '/items', {'name': 'bad', 'stock': -1}),
                ('GET', '/items/1', None),
                ('PUT', '/items/1', {'name': 'Coffee ☕', 'stock': 7}),
                ('POST', '/reservations', {'item_id': 1, 'quantity': 2, 'note': "customer's order"}),
                ('POST', '/reservations', {'item_id': 1, 'quantity': 99, 'note': ''}),
                ('POST', '/reservations', {'item_id': 1, 'quantity': 0, 'note': ''}),
                ('GET', '/reservations', None), ('GET', '/items/999', None),
                ('POST', '/items', {'name': 'temporary', 'stock': 0}),
                ('DELETE', '/items/2', None), ('DELETE', '/items/1', None), ('GET', '/items', None)]
            observed = []
            for method, path, body in sequence:
                pair = [request(services[backend], method, path, body)[0] for backend in ('mairg', 'python')]
                if pair[0] != pair[1]:
                    raise AssertionError({'method': method, 'path': path, 'mairg': pair[0], 'python': pair[1]})
                observed.append({'method': method, 'path': path, 'result': pair[0]})
            before = {backend: (calls[backend]('list_items', []), calls[backend]('list_reservations', []))
                      for backend in services}
            for backend in services:
                try:
                    calls[backend]('fail_reservation', [1, 1, 'must roll back'])
                except Exception as error:
                    assert getattr(error, 'code', None) == 'deliberate_failure'
                else:
                    raise AssertionError('deliberate transaction failure returned success')
                assert before[backend] == (calls[backend]('list_items', []), calls[backend]('list_reservations', []))
            assert before['mairg'] == before['python']
            for backend in services:
                services[backend].close()
                services[backend] = Service(backend, source, base/backend)
                assert before[backend] == (calls[backend]('list_items', []), calls[backend]('list_reservations', []))
            report['correctness'] = {'status': 'pass', 'http_cases': observed,
                                     'rollback': 'pass', 'restart_retains_data': 'pass', 'normalization': 'none'}
            for index in range(30):
                rows = [calls[backend]('create_item', [f'Item {index}', 20]) for backend in services]
                assert rows[0] == rows[1]
            expected = calls['mairg']('list_items', [])
            for service in services.values():
                for _ in range(5):
                    assert request(service, 'GET', '/items')[0] == (200, expected)
            samples = {backend: [] for backend in services}
            for index in range(args.requests):
                for backend in (('mairg', 'python') if index % 2 == 0 else ('python', 'mairg')):
                    result, elapsed = request(services[backend], 'GET', '/items')
                    assert result == (200, expected)
                    samples[backend].append(elapsed)
            for backend, service in services.items():
                started = time.perf_counter()
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                    results = list(pool.map(lambda _: request(service, 'GET', '/items')[0], range(args.requests)))
                elapsed = time.perf_counter() - started
                assert all(result == (200, expected) for result in results)
                cli = []
                for _ in range(3):
                    command = [sys.executable, str(Path(__file__).resolve()), '--invoke', '--backend', backend,
                               '--source', str(source), '--state', str(base/backend)]
                    began = time.perf_counter()
                    done = subprocess.run(command, capture_output=True, text=True, timeout=10, check=True)
                    cli.append((time.perf_counter() - began) * 1000)
                    assert json.loads(done.stdout)['result'] == expected
                report['backends'][backend] = {'sequential_http': distribution(samples[backend]),
                    'concurrent_http_requests_per_second': args.requests/elapsed,
                    'concurrent_clients': 4, 'timed_requests': args.requests, 'response_items': len(expected),
                    'fresh_process_cli': distribution(cli), 'startup_readiness_seconds': service.ready_seconds,
                    'peak_rss_after_requests': service.stats()['peak_rss'],
                    'measurement_scope': 'real HTTP + Waitress + app + SQLite read + JSON + client decode; no kernel isolation'}
        finally:
            for service in services.values():
                service.close()
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'report': str(destination), 'correctness': 'pass',
                      'medians_ms': {name: value['sequential_http']['median_ms'] for name, value in report['backends'].items()}}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
