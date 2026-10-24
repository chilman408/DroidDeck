# SPDX-License-Identifier: GPL-3.0-only
"""Use an existing Git credential-manager login without printing/saving its token."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

OWNER = 'chilman408'
REPO = OWNER + '/DroidDeck'


def main(args):
    parser = argparse.ArgumentParser()
    parser.add_argument('gh', type=Path)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    value = parser.parse_args(args)
    command = value.arguments
    if command[:1] == ['--']:
        command = command[1:]
    if not command or command[0] not in ('api', 'repo', 'release'):
        raise ValueError('Only scoped pilot GitHub API/repository/release commands required')
    env = {**os.environ, 'GCM_INTERACTIVE': 'Never', 'GIT_TERMINAL_PROMPT': '0', 'GH_PROMPT_DISABLED': '1'}
    credential = subprocess.run(['git', '-c', 'credential.interactive=never', 'credential', 'fill'],
        input=('protocol=https\nhost=github.com\nusername=' + OWNER + '\n\n').encode(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=30)
    if credential.returncode:
        raise ValueError('Existing GitHub credential unavailable; interactive user login required')
    fields = dict(line.split('=', 1) for line in credential.stdout.decode().splitlines() if '=' in line)
    token = fields.get('password', '')
    if fields.get('username') != OWNER or not token:
        raise ValueError('Exact existing GitHub owner credential required')
    env['GH_TOKEN'] = token
    profile = subprocess.run([str(value.gh), 'api', 'user', '--jq', '.login'], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=env, timeout=30)
    if profile.returncode or profile.stdout.decode().strip() != OWNER:
        raise ValueError('GitHub authenticated owner verification failed')
    result = subprocess.run([str(value.gh), *command], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, timeout=300)
    for raw, stream in ((result.stdout, sys.stdout), (result.stderr, sys.stderr)):
        stream.write(raw.decode('utf-8', errors='replace').replace(token, '[REDACTED]'))
    return result.returncode


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as error:
        print('Pilot GitHub operation stopped: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
