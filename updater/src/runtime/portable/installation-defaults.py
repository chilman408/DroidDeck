# SPDX-License-Identifier: GPL-3.0-only
"""Shared fresh-install default phase, not service activation or a full installer."""
import importlib.util
import json
import os
from pathlib import Path
import sys


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inspect_media(guest, media):
    try:
        with media.open_journal(media.STATE, create=False) as journal:
            return media.apply(guest, journal, '--inspect')
    except FileNotFoundError:
        expected, current = media.context(guest)
        return {**expected, 'current': current, 'initialized': False, 'changedNow': False}


def execute(guest, apps, media, args, *, lockscreen):
    if not (args == ['--inspect'] or len(args) == 2 and args[0] == '--initialize'):
        raise ValueError('Exact default inspection or initialize APK required')
    # Admit user/data/browser route BEFORE any missing-app installation. Keep the
    # same generation-checked guest adapter for both default requirements.
    media.context(guest)
    lock_result = lockscreen.operate(guest, '--inspect')
    if args != ['--inspect'] and not lock_result['initialized'] and lock_result['secureCredential']:
        raise ValueError('Existing secure lockscreen preserved before default mutations')
    app_result = guest.inspect() if args == ['--inspect'] else apps.install_missing(guest, args[1])
    if not app_result['defaultsSatisfied']:
        return {'kind': 'installation-defaults', 'defaultsSatisfied': False,
                'aurora': app_result, 'media': inspect_media(guest, media), 'lockscreen': lock_result}
    if args == ['--inspect']:
        media_result = inspect_media(guest, media)
    else:
        with media.open_journal(media.STATE, create=True) as journal:
            media_result = media.apply(guest, journal, '--initialize')
        lock_result = lockscreen.operate(guest, '--initialize')
    return {'kind': 'installation-defaults',
            'defaultsSatisfied': media_result['initialized'] and lock_result['initialized'],
            'aurora': app_result, 'media': media_result, 'lockscreen': lock_result}


def main(args):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux installation default phase required')
    apps, media = load('default-apps'), load('default-media')
    result = execute(apps.Guest(), apps, media, args, lockscreen=load('default-lockscreen'))
    print(json.dumps(result, sort_keys=True))
    return 0 if result['defaultsSatisfied'] else 2


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        print('Installation defaults incomplete; preserve existing apps, preferences and journals', file=sys.stderr)
        sys.exit(1)
