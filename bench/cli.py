import json
import logging
import os
import pwd
import subprocess
import sys
from pathlib import Path

import click

from bench.app import get_apps
from bench.commands import bench_command
from bench.config.common_site_config import get_config
from bench.exceptions import PatchError
from bench.utils import drop_privileges, get_cmd_output, get_env_cmd, is_root

logger = logging.getLogger('bench')
from_command_line = False


def cli():
	global from_command_line
	from_command_line = True

	check_uid()
	change_dir()
	change_uid()

	if len(sys.argv) > 1:
		if sys.argv[1] in get_frappe_commands():
			return frappe_cmd()
		elif sys.argv[1] in ("--site", "--verbose", "--force", "--profile"):
			return frappe_cmd()
		elif sys.argv[1] == "--help":
			print(click.Context(bench_command).get_help())
			print()
			print(get_frappe_help())
			return
		elif sys.argv[1] in get_apps():
			return app_cmd()

	try:
		# NOTE: this is the main bench command
		bench_command()
	except PatchError:
		sys.exit(1)


def check_uid():
	if cmd_requires_root() and not is_root():
		logger.error('Superuser privileges required for this command')
		sys.exit(1)


def cmd_requires_root():
	if len(sys.argv) > 2 and sys.argv[2] in ('production', 'sudoers', 'lets-encrypt', 'fonts',
		'print', 'firewall', 'ssh-port', 'role', 'fail2ban', 'wildcard-ssl'):
		return True
	if len(sys.argv) >= 2 and sys.argv[1] in ('patch', 'renew-lets-encrypt', 'disable-production',
		'install'):
		return True


def change_dir():
	if Path('config.json').exists() or "init" in sys.argv:
		return

	dir_path_file = Path('/etc/frappe_bench_dir')
	if dir_path_file.exists():
		dir_path = dir_path_file.read_text().strip()
		if Path(dir_path).exists():
			os.chdir(dir_path.resolve())


def change_uid():
	if is_root() and not cmd_requires_root():
		frappe_user = get_config(".").get('frappe_user')
		if frappe_user:
			drop_privileges(uid_name=frappe_user, gid_name=frappe_user)
			os.environ['HOME'] = pwd.getpwnam(frappe_user).pw_dir
		else:
			print('You should not run this command as root')
			sys.exit(1)


def app_cmd(bench_path='.'):
	python = get_env_cmd('python', bench_path)
	os.chdir(Path(bench_path, 'sites'))
	print(python, [python] + ['-m', 'frappe.utils.bench_helper'] + sys.argv[1:])
	os.execv(python, [python] + ['-m', 'frappe.utils.bench_helper'] + sys.argv[1:])


def frappe_cmd(bench_path='.'):
	python = get_env_cmd('python', bench_path)
	os.chdir(Path(bench_path, 'sites').resolve())
	os.execv(python, [python] + ['-m', 'frappe.utils.bench_helper', 'frappe'] + sys.argv[1:])


def get_frappe_commands(bench_path='.'):
	python = get_env_cmd('python', bench_path)
	sites_path = Path(bench_path, 'sites')

	if not sites_path.exists():
		return []

	try:
		output = get_cmd_output(f"{python} -m frappe.utils.bench_helper get-frappe-commands", cwd=sites_path)
		return json.loads(output)
	except subprocess.CalledProcessError as e:
		if hasattr(e, "stderr"):
			print(e.stderr.decode('utf-8'))
		return []


def get_frappe_help(bench_path='.'):
	python = get_env_cmd('python', bench_path)
	sites_path = Path(bench_path, 'sites')

	if not sites_path.exists():
		return []

	try:
		out = get_cmd_output(f"{python} -m frappe.utils.bench_helper get-frappe-help", cwd=sites_path)
		return "Framework commands:\n" + out.split('Commands:')[1]
	except subprocess.CalledProcessError:
		return ""
