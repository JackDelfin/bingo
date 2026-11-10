#!/usr/bin/env python3
"""Isolated Podman 4.x-shaped test double; never invokes a container runtime."""
import json
import os
from pathlib import Path
import sys
from typing import NoReturn

state_path = Path(os.environ['FAKE_STATE'])
state = json.loads(state_path.read_text())
args = sys.argv[1:]
with open(os.environ['FAKE_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\n')


def done(value=None, code=0) -> NoReturn:
    state_path.write_text(json.dumps(state))
    if value is not None:
        print(value if isinstance(value, str) else json.dumps(value))
    sys.exit(code)


def get_image(name):
    ident = state['tags'].get(name, name)
    return state['images'].get(ident) or state['images'].get(ident.removeprefix('sha256:')) or state['images'].get('sha256:' + ident)


if args[0] == 'info':
    done({'host': {'security': {'rootless': True}}})
if args[:2] == ['container', 'exists']:
    done(code=0 if args[-1] in state['containers'] else 1)
if args[:2] in (['network', 'exists'], ['volume', 'exists']):
    done(code=0 if args[-1] in state[args[0] + 's'] else 1)
if args[:2] in (['image', 'exists'], ['image', 'inspect']):
    image = get_image(args[-1])
    if not image:
        done(code=1)
    done([image] if args[1] == 'inspect' else None)
if args[0] == 'inspect' or args[:2] in (['network', 'inspect'], ['volume', 'inspect']):
    kind = 'containers' if args[0] == 'inspect' else args[0] + 's'
    item = state[kind].get(args[-1])
    if item is None:
        done(code=1)
    if '--format' in args:
        labels = item.get('Config', {}).get('Labels', item.get('labels', item.get('Labels', {})))
        done(labels.get('org.reactbingo.venue', ''))
    done([item])
if args[:2] == ['volume', 'export']:
    if state.get('fail_export'):
        done(code=1)
    Path(args[args.index('--output') + 1]).write_bytes(b'fake-tar-volume-backup')
    done()
if args[0] == 'build':
    state['stage'] = args[-1]
    if state.get('fail_build') and 'frontend' in args[args.index('-f') + 1]:
        done(code=1)
    tag = args[args.index('-t') + 1]
    ident = 'sha256:' + ('c' if 'backend' in tag else 'd') * 64
    state['images'][ident] = {'Id': ident}
    state['tags'][tag] = ident
    done('built')
if args[0] == 'tag':
    image = get_image(args[1])
    if not image:
        done(code=1)
    state['tags'][args[2]] = image['Id']
    done()
if args[0] == 'stop':
    state['containers'][args[-1]]['State']['Running'] = False
    done()
if args[0] == 'rm':
    state['containers'].pop(args[-1], None)
    done()
if args[0] == 'start':
    state['containers'][args[-1]]['State']['Running'] = True
    done()
if args[0] == 'run':
    name = args[args.index('--name') + 1]
    if state.get('fail_app_run') and name == 'venue-bingo-frontend':
        if state.get('extra_mount_before_rollback'):
            state['containers']['venue-bingo-backend']['Mounts'].append({
                'Type': 'volume', 'Name': 'other-persistent-data', 'Destination': '/app/data',
            })
        if state['fail_app_run'] == 'once':
            state['fail_app_run'] = False
        done('CAPABILITYSECRET should never reach installer output', code=1)
    image = get_image(args[-1])
    if name in state['containers'] or image is None:
        done(code=1)
    mounts = []
    for i, arg in enumerate(args):
        if arg == '-v':
            source, dest, *_ = args[i + 1].split(':')
            mounts.append({'Source': source, 'Destination': dest, 'Type': 'bind'})
    state['containers'][name] = {
        'Id': name + '-new', 'Image': image['Id'],
        'Config': {'Labels': {'org.reactbingo.venue': '1'}},
        'State': {'Running': True}, 'Mounts': mounts,
        'NetworkSettings': {'Networks': {'venue-bingo': {}}},
        'HostConfig': {'PortBindings': {'80/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '3000'}]} if name.endswith('frontend') else {}},
    }
    if state.get('fail_marker') and name == 'venue-bingo-frontend':
        (Path(state['stage']).parent / 'update-complete').mkdir(exist_ok=True)
    done(name + '-new')
raise RuntimeError('Unsupported fake Podman call: ' + repr(args))
