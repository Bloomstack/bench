import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import click

import bench
from bench.commands.config import config
from bench.commands.git import remote_reset_url, remote_set_url, remote_urls
from bench.commands.install import install
from bench.commands.make import (exclude_app_for_update, get_app,
	include_app_for_update, init, new_app, remove_app)
from bench.commands.setup import setup
from bench.commands.update import retry_upgrade, switch_to_branch, update
from bench.commands.utils import (backup_all_sites, backup_site, bench_src,
	disable_production, download_translations, prepare_beta_release, release,
	renew_lets_encrypt, restart, set_default_site, set_mariadb_host,
	set_nginx_port, set_ssl_certificate, set_ssl_certificate_key,
	set_url_root, shell, start)
from bench.config.common_site_config import get_config
from bench.utils import exec_cmd, setup_logging, which

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


def print_bench_version(ctx, param, value):
	"Prints current bench version"

	if not value or ctx.resilient_parsing:
		return

	click.echo(bench.__version__)
	ctx.exit()


@click.group()
@click.option('--version', is_flag=True, callback=print_bench_version)
def bench_command(bench_path='.', version=False):
	"Bench manager for Frappe"

	bench.set_frappe_version(bench_path)
	setup_logging(bench_path)


@click.command('migrate-env')
@click.argument('python', type=float)
@click.option('--no-backup', is_flag=True, help='Do not backup the existing Virtual Environment')
def migrate_env(version, no_backup=False):
	"Migrate Virtual Environment to the desired Python Version"

	# This is with the assumption that a bench is set-up within path
	path = Path.cwd()

	# Validate migration params
	python = f"python{version}"
	python = which(python)
	if not python:
		log.error('Invalid Python version...')
		return
	virtualenv = which('virtualenv')
	if not virtualenv:
		log.error('Please install `virtualenv` by running `bench setup requirements`, and try again...')
		return
	pvenv = path.joinpath('env')

	try:
		# clear cache before bench dies
		config = get_config(bench_path=path)
		rredis = urlparse(config['redis_cache'])
		redis = f"{which('redis-cli')} -p {rredis.port}"

		log.debug('Clearing Redis Cache...')
		exec_cmd('{redis} FLUSHALL'.format(redis=redis))
		log.debug('Clearing Redis Database...')
		exec_cmd('{redis} FLUSHDB'.format(redis=redis))
	except Exception:
		log.warn('Please ensure Redis Connections are running or Daemonized.')

	try:
		if not no_backup:
			archive = path.joinpath('archived_envs')
			archive.mkdir(parents=True, exist_ok=True)

			log.debug('Backing up Virtual Environment')
			timestamp = datetime.now().strftime('%Y_%m_%d_%H%M%S')

			# WARNING: This is an archive, you might have to use virtualenv --relocate
			# That's because virtualenv creates symlinks with shebangs pointing to executables.
			# shebangs, shebangs - ricky martin.
			source_dir = pvenv
			dest_dir = archive.joinpath(str(timestamp))
			source_dir.replace(dest_dir)

		log.debug(f"Setting up a New Virtual {python} Environment")
		exec_cmd(f"{virtualenv} --python {python} {pvenv}", cwd=path)

		pip = Path(pvenv, 'bin', 'pip')
		exec_cmd(f"{pip} install --upgrade pip")
		exec_cmd(f"{pip} install --upgrade setuptools")
		# TODO: Options

		papps = path.joinpath('apps')
		apps = [papp for papp in papps.iterdir() if papp.is_dir()]

		for app in apps:
			if app.joinpath('setup.py').exists():
				exec_cmd(f"{pip} install -e {app}")

		log.debug(f"Migration Successful to python{version}")
	except:
		log.debug('Migration Error')
		raise


bench_command.add_command(backup_all_sites)
bench_command.add_command(backup_site)
bench_command.add_command(bench_src)
bench_command.add_command(config)
bench_command.add_command(disable_production)
bench_command.add_command(download_translations)
bench_command.add_command(exclude_app_for_update)
bench_command.add_command(get_app)
bench_command.add_command(include_app_for_update)
bench_command.add_command(init)
bench_command.add_command(install)
bench_command.add_command(migrate_env)
bench_command.add_command(new_app)
bench_command.add_command(prepare_beta_release)
bench_command.add_command(release)
bench_command.add_command(remote_reset_url)
bench_command.add_command(remote_set_url)
bench_command.add_command(remote_urls)
bench_command.add_command(remove_app)
bench_command.add_command(renew_lets_encrypt)
bench_command.add_command(restart)
bench_command.add_command(retry_upgrade)
bench_command.add_command(set_default_site)
bench_command.add_command(set_mariadb_host)
bench_command.add_command(set_nginx_port)
bench_command.add_command(set_ssl_certificate_key)
bench_command.add_command(set_ssl_certificate)
bench_command.add_command(set_url_root)
bench_command.add_command(setup)
bench_command.add_command(shell)
bench_command.add_command(start)
bench_command.add_command(switch_to_branch)
bench_command.add_command(update)
