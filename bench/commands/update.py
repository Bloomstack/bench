import os
import sys

import click

from bench import patches
from bench.app import is_version_upgrade, pull_all_apps, validate_master_branch
from bench.config.common_site_config import get_config, update_config
from bench.utils import (backup_all_sites, before_update, build_assets,
						patch_sites, post_upgrade, pre_upgrade,
						restart_supervisor_processes,
						restart_systemd_processes, update_bench,
						update_node_packages, update_requirements,
						validate_upgrade)


@click.command('update')
@click.option('--pull', is_flag=True, help="Pull changes in all the apps in bench")
@click.option('--patch', is_flag=True, help="Run migrations for all sites in the bench")
@click.option('--build', is_flag=True, help="Build JS and CSS artifacts for the bench")
@click.option('--bench', is_flag=True, help="Update bench")
@click.option('--requirements', is_flag=True, help="Update requirements")
@click.option('--restart-supervisor', is_flag=True, help="restart supervisor processes after update")
@click.option('--restart-systemd', is_flag=True, help="restart systemd units after update")
@click.option('--auto', is_flag=True)
@click.option('--no-backup', is_flag=True)
@click.option('--force', is_flag=True)
@click.option('--reset', is_flag=True, help="Hard resets git branch's to their new states overriding any changes and overriding rebase on pull")
def update(pull=False, patch=False, build=False, bench=False, auto=False,
	restart_supervisor=False, restart_systemd=False, requirements=False,
	no_backup=False, force=False, reset=False):
	"Update bench"

	if not any([pull, patch, build, bench, requirements]):
		pull = patch = build = bench = requirements = True

	if auto:
		sys.exit(1)

	print('\nRunning bench patches...')
	patches.run(bench_path='.')
	print('...done')

	conf = get_config(".")

	if bench and conf.get('update_bench_on_update'):
		print('\nUpdating bench...')
		update_bench()
		print('...done')

	if conf.get('release_bench'):
		print('\nRelease bench, cannot update')
		sys.exit(1)

	validate_master_branch()
	version_upgrade = is_version_upgrade()
	if version_upgrade[0]:
		print()
		print()
		print("This update will cause a major version change in Frappe/ERPNext from {0} to {1}.".format(*version_upgrade[1:]))
		print("This would take significant time to migrate and might break custom apps.")
		click.confirm('Do you want to continue?', abort=True)

	_update(pull, patch, build, bench, auto, restart_supervisor, restart_systemd, requirements, no_backup, force=force, reset=reset)


def _update(pull=False, patch=False, build=False, update_bench=False, auto=False, restart_supervisor=False,
		restart_systemd=False, requirements=False, no_backup=False, bench_path='.', force=False, reset=False):
	conf = get_config(bench_path=bench_path)
	version_upgrade = is_version_upgrade(bench_path=bench_path)

	if version_upgrade[0] or (not version_upgrade[0] and force):
		validate_upgrade(version_upgrade[1], version_upgrade[2], bench_path=bench_path)

	before_update(bench_path=bench_path, requirements=requirements)

	conf.update({"maintenance_mode": 1, "pause_scheduler": 1})
	update_config(conf, bench_path=bench_path)

	if not no_backup:
		print('\nBacking up sites...')
		backup_all_sites(bench_path=bench_path)
		print('...done')

	if pull:
		print('\nPulling changes for apps...')
		pull_all_apps(bench_path=bench_path, reset=reset)
		print('...done')

	if requirements:
		update_requirements(bench_path=bench_path)
		update_node_packages(bench_path=bench_path)

	if version_upgrade[0] or (not version_upgrade[0] and force):
		pre_upgrade(version_upgrade[1], version_upgrade[2], bench_path=bench_path)
		import bench.utils
		import bench.app
		print('Reloading bench...')
		if sys.version_info >= (3, 4):
			import importlib
			importlib.reload(bench.utils)
			importlib.reload(bench.app)
		else:
			reload(bench.utils)
			reload(bench.app)

	if patch:
		print('\nPatching sites...')
		patch_sites(bench_path=bench_path)
		print('...done')
	if build:
		print('\nBuilding assets...')
		build_assets(bench_path=bench_path)
		print('...done')
	if version_upgrade[0] or (not version_upgrade[0] and force):
		post_upgrade(version_upgrade[1], version_upgrade[2], bench_path=bench_path)
	if restart_supervisor or conf.get('restart_supervisor_on_update'):
		restart_supervisor_processes(bench_path=bench_path)
	if restart_systemd or conf.get('restart_systemd_on_update'):
		restart_systemd_processes(bench_path=bench_path)

	conf.update({"maintenance_mode": 0, "pause_scheduler": 0})
	update_config(conf, bench_path=bench_path)

	print("_"*80)
	print("Bench: Deployment tool for Frappe and ERPNext (https://erpnext.org).")
	print("Open source depends on your contributions, so please contribute bug reports, patches, fixes or cash and be a part of the community")
	print()


@click.command('retry-upgrade')
@click.option('--version', default=5)
def retry_upgrade(version):
	pull_all_apps()
	patch_sites()
	build_assets()
	post_upgrade(version - 1, version)


@click.command('switch-to-branch')
@click.argument('branch')
@click.argument('apps', nargs=-1)
@click.option('--upgrade', is_flag=True)
def switch_to_branch(branch, apps, upgrade=False):
	"Switch all apps to specified branch, or specify apps separated by space"
	from bench.app import switch_to_branch
	switch_to_branch(branch=branch, apps=list(apps), upgrade=upgrade)
	print('Switched to ' + branch)
	print('Please run `bench update --patch` to be safe from any differences in database schema')


@click.command('switch-to-master')
def switch_to_master():
	"Switch frappe and erpnext to master branch"
	from bench.app import switch_to_master
	switch_to_master(apps=['frappe', 'erpnext'])
	print()
	print('Switched to master')
	print('Please run `bench update --patch` to be safe from any differences in database schema')


@click.command('switch-to-develop')
def switch_to_develop(upgrade=False):
	"Switch frappe and erpnext to develop branch"
	from bench.app import switch_to_develop
	switch_to_develop(apps=['frappe', 'erpnext'])
	print()
	print('Switched to develop')
	print('Please run `bench update --patch` to be safe from any differences in database schema')
