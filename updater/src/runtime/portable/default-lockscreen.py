# SPDX-License-Identifier: GPL-3.0-only
"""One-time Swipe-to-None default; never clear or validate a user credential."""
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = load('default-media')
STATE = '/var/lib/droiddeck/default-lockscreen'
GET = ['/system/bin/locksettings', 'get-disabled', '--user', '0']
SET = ['/system/bin/locksettings', 'set-disabled', '--user', '0', 'true']


def keyguard(output):
    blocks = re.findall(r'^[ \t]*KeyguardServiceDelegate\n(.*?)(?=^[ \t]*KeyguardStateMonitor$)',
                        output, re.M | re.S)
    if len(blocks) != 1:
        raise ValueError('Unambiguous supported Android keyguard state required')
    result = {}
    for key in ('secure', 'showing', 'bootCompleted', 'systemIsReady'):
        values = re.findall(r'^[ \t]*' + key + r'=(true|false)$', blocks[0], re.M)
        if len(values) != 1:
            raise ValueError('Exact keyguard state booleans required')
        result[key] = values[0] == 'true'
    if not result['bootCompleted'] or not result['systemIsReady']:
        raise ValueError('Booted Android keyguard required')
    return result


def context(guest):
    if (guest.command(['/system/bin/am', 'get-current-user']) != '0'
            or guest.command(['/system/bin/getprop', 'ro.build.version.sdk']) != '36'):
        raise ValueError('Supported Android SDK36 user0 required')
    identity = guest.command(['/system/bin/stat', '-c', '%d:%i', '/data'])
    if not re.fullmatch('[0-9]+:[1-9][0-9]*', identity):
        raise ValueError('Original Android data identity required')
    state = keyguard(guest.command(['/system/bin/dumpsys', 'window', 'policy']))
    disabled = None
    # Do not issue locksettings commands or empty-credential attempts when a
    # secure keyguard is observed. Later user credentials remain authoritative.
    if not state['secure']:
        value = guest.command(GET)
        if value not in ('true', 'false'):
            raise ValueError('Exact Android lockscreen-disabled response required')
        disabled = value == 'true'
    expected = {'version': 1, 'kind': 'lockscreen-default', 'androidData': identity, 'user': 0, 'sdk': 36}
    return expected, {'secureCredential': state['secure'], 'keyguardShowing': state['showing'],
                      'lockscreenDisabled': disabled}


def apply(guest, journal, mode, *, sleep=time.sleep):
    if mode not in ('--inspect', '--initialize', '--resume'):
        raise ValueError('Exact lockscreen default operation required')
    expected, state = context(guest)
    intent, complete = journal.read('intent.json'), journal.read('complete.json')
    if (intent is not None and M.encode(intent) != M.encode(expected)
            or complete is not None and M.encode(complete) != M.encode(expected)
            or complete is not None and intent is None):
        raise ValueError('Original lockscreen default context and intent required')
    result = {**expected, **state, 'initialized': complete is not None, 'changedNow': False}
    if complete is not None or mode == '--inspect':
        return result
    if state['secureCredential']:
        raise ValueError('Existing secure lockscreen preserved; never clear or verify credentials')
    if intent is None:
        if mode != '--initialize':
            raise ValueError('No pending lockscreen initialization to resume')
        journal.write('intent.json', expected)
        actual, before = context(guest)
        if actual != expected or before['secureCredential']:
            raise ValueError('Android data or credential state changed before initialization')
        journal.check()
        if not before['lockscreenDisabled']:
            if guest.command(SET) != 'Lock screen disabled set to true':
                raise ValueError('Android did not confirm the lockscreen default')
            result['changedNow'] = True
    elif mode != '--resume':
        raise ValueError('Pending lockscreen default requires explicit confirm-only resume')
    for attempt in range(11):
        actual, state = context(guest)
        if actual != expected or state['secureCredential']:
            raise ValueError('Original Android data without secure credentials required')
        if state['lockscreenDisabled']:
            journal.write('complete.json', expected)
            return {**result, **state, 'initialized': True}
        if attempt != 10:
            sleep(0.5)
    raise ValueError('Lockscreen setting not confirmed; retain original intent')


def operate(guest, mode):
    if mode not in ('--inspect', '--initialize', '--resume'):
        raise ValueError('Exact lockscreen default operation required')
    try:
        with M.open_journal(STATE, create=mode == '--initialize') as journal:
            return apply(guest, journal, mode)
    except FileNotFoundError:
        if mode != '--inspect':
            raise
        expected, state = context(guest)
        return {**expected, **state, 'initialized': False, 'changedNow': False}


def main(args):
    if sys.platform != 'linux' or os.geteuid() != 0 or len(args) != 1:
        raise ValueError('Root Linux lockscreen default operation required')
    result = operate(load('default-apps').Guest(), args[0])
    print(json.dumps(result, sort_keys=True))
    return 0 if result['initialized'] else 2


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        print('Lockscreen default incomplete; preserve user credentials, choices and original journals', file=sys.stderr)
        sys.exit(1)
