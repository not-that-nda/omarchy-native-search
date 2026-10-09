#!/usr/bin/env python3
"""Versioned JSON-lines helper. No query/path logging or permanent daemon."""
import asyncio
import json
import os
import subprocess
import sys
import threading
from ns_config import CONFIG, load, validate, write_json
from ns_index import refresh, status
from ns_search import Search
from ns_actions import action
from ns_preview import Preview


async def serve():
    engine = Search()
    preview = Preview()
    preview_cancel = threading.Event()
    search_task = None
    cancel = threading.Event()
    jobs = set()

    def emit(request_id, operation, value=None, error=None):
        response = {'version': 1, 'request_id': request_id, 'operation': operation,
                    'type': 'error' if error else 'complete'}
        response.update({'error': error} if error else (value or {}))
        print(json.dumps(response, ensure_ascii=True), flush=True)

    async def run(request, token):
        request_id, operation = request['request_id'], request['operation']
        args = request.get('arguments', {})
        try:
            config = load()
            if operation == 'search':
                result = await asyncio.to_thread(engine.query, config, args, token)
                if token.is_set():
                    return
            elif operation == 'status':
                result = {'roots': await asyncio.to_thread(status, config)}
            elif operation == 'settings-read':
                result = {'settings': config}
            elif operation == 'settings-write':
                result = {'settings': validate(args.get('settings'))}
                write_json(CONFIG, result['settings'])
            elif operation == 'refresh':
                ids = args.get('roots', [])
                if not isinstance(ids, list) or any(i not in [r['id'] for r in config['roots']] for i in ids):
                    raise ValueError('Unknown refresh roots')
                # Refresh survives popup closure, owned by the user manager.
                await asyncio.to_thread(subprocess.run, ['systemctl', '--user', 'start', '--no-block',
                                        'nda-native-search-refresh.service'], check=True,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                result = {'message': 'Refresh requested; last successful snapshot remains searchable'}
            elif operation == 'action':
                result = await asyncio.to_thread(action, engine, config, args)
            elif operation == 'preview':
                result = await asyncio.to_thread(preview.render, engine, config, args, token)
            else:
                raise ValueError('Unknown operation')
            emit(request_id, operation, result)
        except InterruptedError:
            pass
        except (OSError, ValueError, TimeoutError, subprocess.SubprocessError, KeyError, TypeError) as error:
            if not token.is_set():
                emit(request_id, operation, error=str(error)[:512])

    while True:
        line = await asyncio.to_thread(sys.stdin.buffer.readline, 65537)
        if not line:
            break
        try:
            if len(line) > 65536 or not line.endswith(b'\n'):
                raise ValueError('Protocol line exceeds 64KiB or is incomplete')
            request = json.loads(line)
            if not isinstance(request, dict) or type(request.get('version')) is not int or request['version'] != 1:
                raise ValueError('Unsupported protocol version')
            rid = request.get('request_id')
            if type(rid) not in (str, int) or len(str(rid)) > 64:
                raise ValueError('Invalid request ID')
            if not isinstance(request.get('arguments', {}), dict):
                raise ValueError('Arguments must be an object')
            operation = request.get('operation')
            if operation in ('search', 'cancel'):
                preview_cancel.set()
                cancel.set()
                if search_task:
                    await search_task
                cancel = threading.Event()
                engine.identities = {}
                if operation == 'cancel':
                    emit(rid, operation, {'cancelled': True})
                    continue
            if len(jobs) >= 8:
                raise ValueError('Too many concurrent requests')
            if operation == 'preview':
                preview_cancel.set()
                preview_cancel = threading.Event()
            task = asyncio.create_task(run(request, cancel if operation == 'search' else
                                           preview_cancel if operation == 'preview' else threading.Event()))
            jobs.add(task)
            task.add_done_callback(jobs.discard)
            if operation == 'search':
                search_task = task
        except (ValueError, TypeError) as error:
            emit(None, 'protocol', error=str(error))
            if len(line) > 65536:
                break
    cancel.set()
    await asyncio.gather(*jobs)


def main():
    os.umask(0o077)
    command = sys.argv[1] if len(sys.argv) > 1 else 'serve'
    if command == 'serve':
        asyncio.run(serve())
    elif command == 'refresh':
        result = refresh(load(), scheduled='--scheduled' in sys.argv)
        # Operational counts/timings only; roots/config remain private on disk.
        print(json.dumps([{'id': r['id'], 'status': r['status'],
                           'duration_seconds': r.get('duration_seconds')} for r in result]))
        if any(r['status'] == 'failed' for r in result):
            raise SystemExit(1)
    elif command == 'status':
        print(json.dumps(status(load()), indent=2))
    else:
        raise ValueError('Unknown helper command')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({'error': str(error)[:512]}))
        raise SystemExit(2)
