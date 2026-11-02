"""Create local Docker secret files without printing or replacing credentials."""
import argparse
import os
from pathlib import Path
import secrets
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--directory', type=Path, required=True)
args = parser.parse_args()
directory = args.directory.resolve()
directory.mkdir(parents=True, exist_ok=True)
if os.name == 'nt':
    identity = subprocess.check_output(['whoami'], text=True).strip()
    subprocess.run(['icacls', str(directory), '/inheritance:r', '/grant:r',
                    f'{identity}:(OI)(CI)F', 'SYSTEM:(OI)(CI)F'], check=True, stdout=subprocess.DEVNULL)
else:
    directory.chmod(0o700)
for filename in ('mysql-root-password', 'mysql-password'):
    path = directory / filename
    if not path.exists():
        path.write_text(secrets.token_urlsafe(36), encoding='utf-8')
    if os.name != 'nt':
        path.chmod(0o600)
client = directory / 'mysql-root-client.cnf'
if not client.exists():
    client.write_text('[client]\nuser=root\npassword=' + (directory / 'mysql-root-password').read_text() + '\nhost=localhost\n', encoding='utf-8')
if os.name != 'nt':
    client.chmod(0o600)
print(f'Secret files are ready in {directory}; existing values were preserved.')
