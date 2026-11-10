#!/usr/bin/env python3
"""Fail-closed rootless Podman-shaped fixture. No runtime delegation exists."""
import json
import os
from pathlib import Path
import sys

path = Path(os.environ['FAKE_STATE'])
s = json.loads(path.read_text())
a = sys.argv[1:]
with Path(os.environ['FAKE_LOG']).open('a') as log:
    log.write(json.dumps(a) + '\n')


def done(value=None, code=0):
    path.write_text(json.dumps(s))
    if value is not None:
        print(value if isinstance(value, str) else json.dumps(value))
    sys.exit(code)


def image(name):
    return s['images'].get(s['tags'].get(name, name))


if a[0] == 'info':
    done({'host': {'security': {'rootless': s.get('rootless', True)}},
          'store': s.get('store', {'graphRoot': '/fake/private/store', 'runRoot': '/fake/private/run',
                     'graphDriverName': 'overlay',
                     'containerStore': {'number': len(s['containers']), 'running': sum(c.get('State', {}).get('Running', False) for c in s['containers'].values()), 'paused': 0, 'stopped': 0},
                     'imageStore': {'number': len(s['images'])},
                     'graphRootUsed': s.get('used_bytes', 4096) + len(s['images']) * 2048,
                     'graphRootAllocated': 1048576})})
if len(a) > 1 and a[1] == 'exists':
    exists = image(a[-1]) is not None if a[0] == 'image' else a[-1] in s[a[0] + 's']
    done(code=0 if exists else 1)
if a[0] == 'inspect' or len(a) > 1 and a[1] == 'inspect':
    kind = 'containers' if a[0] == 'inspect' else a[0] + 's'
    item = image(a[-1]) if kind == 'images' else s[kind].get(a[-1])
    if item is None:
        done(code=1)
    if '--format' in a:
        done(item.get('Config', {}).get('Labels', item.get('labels', item.get('Labels', {}))).get('org.reactbingo.venue', ''))
    done([item])
if a[0] == 'pull':
    ident = 'sha256:' + 'f' * 64
    s['images'][ident] = {'Id': ident, 'RepoTags': [a[-1]]}
    s['tags'][a[-1]] = ident
    done(ident)
if a[0] == 'build':
    if s.get('fail_build') and 'frontend' in a[a.index('-f') + 1]:
        done('suppressed test private output', 1)
    tag = a[a.index('-t') + 1]
    ident = 'sha256:' + format(s.get('build_counter', 0) + 1, '064x')
    s['build_counter'] = s.get('build_counter', 0) + 1
    s['images'][ident] = {'Id': ident, 'Config': {'User': 'node' if 'backend' in tag else ''}}
    s['tags'][tag] = ident
    if s.get('collide_after_build') and 'frontend' in tag:
        s['volumes']['venue-bingo-data'] = {'Labels': {'org.reactbingo.venue': '1'}, 'CreatedAt': 'unexpected-volume'}
    done(ident)
if a[0] == 'tag':
    item = image(a[1])
    if item is None:
        done(code=1)
    s['tags'][a[2]] = item['Id']
    done()
if a[:2] in (['network', 'create'], ['volume', 'create']):
    if a[-1] in s[a[0] + 's']:
        done(code=1)
    s[a[0] + 's'][a[-1]] = {('labels' if a[0] == 'network' else 'Labels'): {'org.reactbingo.venue': '1'},
                           'Name': a[-1], 'Id': 'test-' + a[-1], 'CreatedAt': 'test-created'}
    done(a[-1])
if a[:2] == ['volume', 'export']:
    Path(a[a.index('--output') + 1]).write_bytes(b'fake-volume-export-not-consistent-backup')
    done()
if a[0] in ('start', 'stop', 'rm'):
    if a[-1] not in s['containers']:
        done(code=1)
    if a[0] == 'start':
        if s.get('fail_start') == a[-1]:
            done('suppressed private runtime output', 1)
        if s.get('noop_start') == a[-1]:
            done()
        if s.get('replace_on_start') == a[-1]:
            s['containers']['venue-bingo-frontend']['Id'] = 'late-replacement'
    if a[0] == 'rm':
        del s['containers'][a[-1]]
    else:
        s['containers'][a[-1]]['State']['Running'] = a[0] == 'start'
    done()
if a[0] == 'run':
    name = a[a.index('--name') + 1]
    if s.get('fail_app_run') and name.endswith('frontend'):
        if s['fail_app_run'] == 'once':
            s['fail_app_run'] = False
        done('suppressed test private output', 1)
    item = image(a[-1])
    if item is None or name in s['containers']:
        done(code=1)
    mounts = []
    for i, word in enumerate(a):
        if word == '-v':
            source, dest, *mode = a[i + 1].split(':')
            mounts.append({'Type': 'volume' if source == 'venue-bingo-data' else 'bind',
                           'Name': source if source == 'venue-bingo-data' else '',
                           'Source': source, 'Destination': dest, 'RW': not mode or 'ro' not in mode[0]})
    s['containers'][name] = {
        'Id': name + '-instance-' + str(s.get('build_counter', 0)), 'Image': item['Id'],
        'Config': {'Labels': {'org.reactbingo.venue': '1'}, 'User': item.get('Config', {}).get('User', ''), 'Env': ['GLIBC_TUNABLES=glibc.pthread.rseq=1'] if name.endswith('db') else []},
        'State': {'Running': True}, 'Mounts': mounts,
        'NetworkSettings': {'Networks': {'venue-bingo': {}}, 'Ports': (
            {'80/tcp': [{'HostIp': s.get('frontend_host_ip', '0.0.0.0'), 'HostPort': '3000'}]} if name.endswith('frontend')
            else {'8999/tcp': None} if name.endswith('backend') else {'27017/tcp': None})},
        'HostConfig': {'PortBindings': {'80/tcp': [{'HostIp': s.get('frontend_host_ip', '0.0.0.0'), 'HostPort': '3000'}]} if name.endswith('frontend') else {}},
    }

    # DAC simulation only: default rootless mapping makes the invoking host
    # owner container root, not USER node. Bind mounting the file bypasses its
    # private host parents, but a nonroot backend still needs other-read access.
    # This is not actual UID, SELinux, image-build or rootless Podman proof.
    if name.endswith('backend') and item.get('Config', {}).get('User') == 'node':
        cards = next(m for m in mounts if m['Destination'] == '/app/cards.csv')
        s['containers'][name]['State']['Running'] = bool(os.stat(cards['Source']).st_mode & 0o004)
    done(s['containers'][name]['Id'])
done('Unsupported test runtime command', 123)
