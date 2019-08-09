from __future__ import print_function

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import git
import requests
import semantic_version

import bench
from bench.config.common_site_config import get_config
from bench.exceptions import InvalidBranchException, InvalidRemoteException, MajorVersionUpgradeException
from bench.utils import (CommandFailedError, build_assets,
						check_git_for_shallow_clone, exec_cmd, get_cmd_output,
						get_commits_to_pull, get_env_cmd, get_frappe,
						restart_supervisor_processes,
						restart_systemd_processes, run_frappe_cmd)

logging.basicConfig(level="DEBUG")
logger = logging.getLogger(__name__)


def get_apps(bench_path='.'):
	try:
		apps_txt = Path(bench_path, 'sites', 'apps.txt')
		return apps_txt.read_text().splitlines()
	except IOError:
		return []


def add_to_appstxt(app, bench_path='.'):
	apps = get_apps(bench_path)
	if app not in apps:
		apps.append(app)
		return write_appstxt(apps, bench_path)


def remove_from_appstxt(app, bench_path='.'):
	apps = get_apps(bench_path)
	if app in apps:
		apps.remove(app)
		return write_appstxt(apps, bench_path)


def write_appstxt(apps, bench_path='.'):
	apps_txt = Path(bench_path, 'sites', 'apps.txt')
	apps_txt.write_text('\n'.join(apps))


def check_url(url, raise_err=True):
	try:
		from urlparse import urlparse
	except ImportError:
		from urllib.parse import urlparse

	parsed = urlparse(url)
	if not parsed.scheme:
		if raise_err:
			raise TypeError('{url} Not a valid URL'.format(url=url))
		else:
			return False

	return True


def get_excluded_apps(bench_path='.'):
	excluded_apps_file = Path(bench_path, 'sites', 'excluded_apps.txt')

	if excluded_apps_file.exists():
		return excluded_apps_file.read_text().splitlines()
	else:
		return []


def add_to_excluded_apps_txt(app, bench_path='.'):
	if app == 'frappe':
		raise ValueError('Frappe app cannot be excluded from update')

	if app not in [dir for dir in Path('apps').iterdir() if dir.is_dir()]:
		raise ValueError('The app "{}" does not exist'.format(app))

	apps = get_excluded_apps(bench_path)
	if app not in apps:
		apps.append(app)
		return write_excluded_apps_txt(apps, bench_path)


def remove_from_excluded_apps_txt(app, bench_path='.'):
	apps = get_excluded_apps(bench_path)
	if app in apps:
		apps.remove(app)
		return write_excluded_apps_txt(apps, bench_path)


def write_excluded_apps_txt(apps, bench_path='.'):
	excluded_apps_file = Path(bench_path, 'sites', 'excluded_apps.txt')
	excluded_apps_file.write_text('\n'.join(apps))


def get_app(git_url, branch=None, bench_path='.', build_asset_files=True, verbose=False, postprocess=True):
	try:
		from urlparse import urljoin
	except ImportError:
		from urllib.parse import urljoin

	if not check_url(git_url, raise_err=False):
		orgs = ['frappe', 'erpnext']
		for org in orgs:
			url = f'https://api.github.com/repos/{org}/{git_url}'
			res = requests.get(url)
			if res.ok:
				data = res.json()
				if 'name' in data:
					if git_url == data['name']:
						git_url = f'https://github.com/{org}/{git_url}'
						break

	# Gets repo name from URL
	repo_name = git_url.rsplit('/', 1)[1].rsplit('.', 1)[0]
	logger.info(f'getting app {repo_name}')
	shallow_clone = '--depth 1' if check_git_for_shallow_clone() else ''
	branch = f'--branch {branch}' if branch else ''

	exec_cmd(f"git clone {git_url} {branch} {shallow_clone} --origin upstream", cwd=Path(bench_path, 'apps'))

	# Retrieves app name from setup.py
	app_path = Path(bench_path, 'apps', repo_name, 'setup.py')
	app_name = re.search(r'name\s*=\s*[\'"](.*)[\'"]', app_path.read_text()).group(1)

	if repo_name != app_name:
		apps_path = Path(bench_path).resolve().joinpath('apps')
		Path(apps_path, repo_name).rename(Path(apps_path, app_name))

	print('installing', app_name)
	install_app(app_name, bench_path, verbose)

	if postprocess:
		if build_asset_files:
			build_assets(bench_path, app_name)

		conf = get_config(bench_path)

		if conf.get('restart_supervisor_on_update'):
			restart_supervisor_processes(bench_path)
		if conf.get('restart_systemd_on_update'):
			restart_systemd_processes(bench_path)


def new_app(app, bench_path='.'):
	# For backwards compatibility
	app = app.lower().replace(" ", "_").replace("-", "_")
	logger.info(f'creating new app {app}')
	apps_dir = Path(bench_path, 'apps').resolve()
	bench.set_frappe_version(bench_path=bench_path)

	if bench.FRAPPE_VERSION == 4:
		exec_cmd(f"{get_frappe(bench_path)} --make_app {apps_dir} {app}")
	else:
		run_frappe_cmd('make-app', apps_dir, app, bench_path)

	install_app(app, bench_path)


def install_app(app, bench_path='.', verbose=False, no_cache=False):
	logger.info(f'Installing {app}...')
	find_links = ''

	pip = get_env_cmd('pip', bench_path)
	quiet = "-q" if not verbose else ""
	no_cache = '--no-cache-dir' if no_cache else ""
	app_dir = Path(bench_path, 'apps', app)

	exec_cmd(f"{pip} install {quiet} {find_links} -e {app_dir} {no_cache}")
	add_to_appstxt(app, bench_path)


def remove_app(app, bench_path='.'):
	if not app in get_apps(bench_path):
		print(f"No app named {app}")
		sys.exit(1)

	pip = get_env_cmd('pip', bench_path)
	app_path = Path(bench_path, 'apps', app)
	site_path = Path(bench_path, 'sites')

	for site in site_path.iterdir():
		req_file = Path(site_path, site, 'site_config.json')
		if req_file.exists():
			out = subprocess.check_output(["bench", "--site", site, "list-apps"], cwd=bench_path).decode('utf-8')
			if re.search(r'\b' + app + r'\b', out):
				print(f"Cannot remove, app is installed on site: {site}")
				sys.exit(1)

	exec_cmd(f"{pip} uninstall -y {app}")  # remove the app from installed sites
	remove_from_appstxt(app, bench_path)  # remove the app from apps.txt
	shutil.rmtree(app_path)  # delete the app folder
	run_frappe_cmd("build", bench_path=bench_path)  # rebuild the site assets

	if get_config(bench_path).get('restart_supervisor_on_update'):
		restart_supervisor_processes(bench_path)
	if get_config(bench_path).get('restart_systemd_on_update'):
		restart_systemd_processes(bench_path)


def pull_all_apps(bench_path='.', reset=False):
	'''Check all apps if there no local changes, pull'''
	rebase = '--rebase' if get_config(bench_path).get('rebase_on_pull') else ''

	# check for local changes
	excluded_apps = get_excluded_apps()
	for app in get_apps(bench_path):
		if app in excluded_apps:
			print("Skipping update for app {}".format(app))
			continue

		repo_dir = get_repo_dir(app, bench_path)
		remote = get_remote(app)
		branch = get_current_branch(app, bench_path)

		if not remote:
			# remote doesn't exist, add the app to excluded_apps.txt
			add_to_excluded_apps_txt(app, bench_path)
			print("Skipping pull for app '{}', since remote doesn't exist, and adding it to excluded apps".format(app))
			continue

		commit_count = get_commits_to_pull(repo_dir, remote, branch)
		if commit_count == 0:
			print("...no updates for '{}'".format(app))
			continue

		try:
			repo = git.Repo(repo_dir)
		except git.exc.InvalidGitRepositoryError as e:
			continue

		if not reset:
			is_modified = repo.index.diff(None)
			is_staged = repo.index.diff("HEAD")
			is_untracked = repo.untracked_files

			if any([is_modified, is_staged, is_untracked]):
				print('''
Cannot proceed with update: You have local changes in app "{0}" that are not committed.

Here are your choices:

1. Merge the {0} app manually with "git pull" / "git pull --rebase" and fix conflicts.
2. Temporarily remove your changes with "git stash" or discard them completely
with "bench update --reset" or for individual repositries "git reset --hard"
3. If your changes are helpful for others, send in a pull request via GitHub and
wait for them to be merged in the core.
				'''.format(app))
				sys.exit(1)

		print('...{0}...'.format(app))

		if reset:
			repo.git.fetch("-all")
			repo.git.reset("--hard", "{remote}/{branch}".format(remote=remote, branch=branch))
		elif rebase:
			repo.git.pull(rebase, remote, branch)
		else:
			repo.git.pull(remote, branch)

		# display diff from the pulled commits
		print(repo.git.diff("--stat", "HEAD~{}".format(commit_count)), "HEAD")

		# remove compiled Python files from the app
		[path.unlink() for path in repo_dir.rglob('*.py[co]')]


def is_version_upgrade(app='frappe', bench_path='.', branch=None):
	print("\nChecking for version upgrades for {app}...".format(app=app))

	try:
		fetch_upstream(app, bench_path)
	except CommandFailedError:
		raise InvalidRemoteException("No remote named 'upstream' for {0}".format(app))

	upstream_version = get_upstream_version(app, branch, bench_path)

	if not upstream_version:
		raise InvalidBranchException("Specified branch of app {0} is not in the 'upstream' remote".format(app))

	local_version = get_major_version(get_current_version(app, bench_path))
	upstream_version = get_major_version(upstream_version)

	version_upgrade = False
	if upstream_version - local_version > 0:
		print("...new version found")
		version_upgrade = False

	print("...already on latest version")
	return (version_upgrade, local_version, upstream_version)


def get_current_frappe_version(bench_path='.'):
	try:
		return get_major_version(get_current_version('frappe', bench_path))
	except IOError:
		return 0


def get_current_branch(app, bench_path='.'):
	repo_dir = get_repo_dir(app, bench_path)
	repo = git.Repo(repo_dir)
	return repo.active_branch


def get_remote(app, bench_path='.'):
	repo_dir = get_repo_dir(app, bench_path)
	repo = git.Repo(repo_dir)
	remotes = [remote.name for remote in repo.remotes]

	if not remotes:
		return False
	elif 'upstream' in remotes:
		return 'upstream'
	else:
		return repo.remote()


def use_rq(bench_path):
	bench_path = Path(bench_path).resolve()
	celery_app = bench_path.joinpath('apps', 'frappe', 'frappe', 'celery_app.py')
	return not celery_app.exists()


def fetch_upstream(app, bench_path='.'):
	repo_dir = get_repo_dir(app, bench_path)
	repo = git.Repo(repo_dir)
	repo.git.fetch("upstream")


def get_current_version(app, bench_path='.'):
	repo_dir = get_repo_dir(app, bench_path)
	try:
		version_file = Path(repo_dir, repo_dir.name, '__init__.py')
		return get_version_from_string(version_file.read_text())
	except AttributeError:
		# backward compatibility
		version_file = Path(repo_dir, 'setup.py')
		return get_version_from_string(version_file.read_text(), field='version')


def get_develop_version(app, bench_path='.'):
	repo_dir = get_repo_dir(app, bench_path=bench_path)
	with open(os.path.join(repo_dir, os.path.basename(repo_dir), 'hooks.py')) as f:
		return get_version_from_string(f.read(), field='develop_version')


def get_upstream_version(app, branch=None, bench_path='.'):
	repo_dir = get_repo_dir(app, bench_path)
	repo = git.Repo(repo_dir)

	if not branch:
		branch = get_current_branch(app, bench_path)

	try:
		contents = repo.git.show('upstream/{branch}:{app}/__init__.py'.format(branch=branch, app=app))
	except git.exc.GitCommandError as e:
		contents = None

	return get_version_from_string(contents) if contents else None


def get_upstream_url(app, bench_path='.'):
	repo_dir = get_repo_dir(app, bench_path)
	repo = git.Repo(repo_dir)
	return repo.git.config("--get", 'remote.upstream.url')


def get_repo_dir(app, bench_path='.'):
	repo_dir = Path(bench_path, 'apps', app)

	if not repo_dir.exists():
		print("\nThe `{}` app does not exist".format(app))
		sys.exit(1)

	return repo_dir


def switch_branch(branch, apps=None, bench_path='.', upgrade=False, check_upgrade=True):
	from bench.utils import update_requirements, update_node_packages, backup_all_sites, patch_sites, build_assets, pre_upgrade, post_upgrade
	from . import utils
	apps_dir = os.path.join(bench_path, 'apps')
	version_upgrade = (False,)
	switched_apps = []

	if not apps:
		apps = [name for name in os.listdir(apps_dir)
			if os.path.isdir(os.path.join(apps_dir, name))]
		if branch == "v4.x.x":
			apps.append('shopping_cart')

	for app in apps:
		app_dir = os.path.join(apps_dir, app)
		if os.path.exists(app_dir):
			try:
				if check_upgrade:
					version_upgrade = is_version_upgrade(app=app, bench_path=bench_path, branch=branch)
					if version_upgrade[0] and not upgrade:
						raise MajorVersionUpgradeException("Switching to {0} will cause upgrade from {1} to {2}. Pass --upgrade to confirm".format(branch, version_upgrade[1], version_upgrade[2]), version_upgrade[1], version_upgrade[2])
				print("Switching for "+app)
				unshallow = "--unshallow" if os.path.exists(os.path.join(app_dir, ".git", "shallow")) else ""
				exec_cmd("git config --unset-all remote.upstream.fetch", cwd=app_dir)
				exec_cmd("git config --add remote.upstream.fetch '+refs/heads/*:refs/remotes/upstream/*'", cwd=app_dir)
				exec_cmd("git fetch upstream {unshallow}".format(unshallow=unshallow), cwd=app_dir)
				exec_cmd("git checkout {branch}".format(branch=branch), cwd=app_dir)
				exec_cmd("git merge upstream/{branch}".format(branch=branch), cwd=app_dir)
				switched_apps.append(app)
			except CommandFailedError:
				print("Error switching to branch {0} for {1}".format(branch, app))
			except InvalidRemoteException:
				print("Remote does not exist for app "+app)
			except InvalidBranchException:
				print("Branch {0} does not exist in Upstream for {1}".format(branch, app))

	if switched_apps:
		print("Successfully switched branches for:\n" + "\n".join(switched_apps))

	if version_upgrade[0] and upgrade:
		update_requirements()
		update_node_packages()
		pre_upgrade(version_upgrade[1], version_upgrade[2])
		if sys.version_info >= (3, 4):
			import importlib
			importlib.reload(utils)
		else:
			reload(utils)
		backup_all_sites()
		patch_sites()
		build_assets()
		post_upgrade(version_upgrade[1], version_upgrade[2])


def switch_to_branch(branch=None, apps=None, bench_path='.', upgrade=False):
	switch_branch(branch, apps=apps, bench_path=bench_path, upgrade=upgrade)


def switch_to_master(apps=None, bench_path='.', upgrade=True):
	switch_branch('master', apps=apps, bench_path=bench_path, upgrade=upgrade)


def switch_to_develop(apps=None, bench_path='.', upgrade=True):
	switch_branch('develop', apps=apps, bench_path=bench_path, upgrade=upgrade)


def get_version_from_string(contents, field='__version__'):
	match = re.search(r"^(\s*%s\s*=\s*['\\\"])(.+?)(['\"])(?sm)" % field, contents)
	return match.group(2)


def get_major_version(version):
	return semantic_version.Version(version).major


def install_apps_from_path(path, bench_path='.'):
	apps = get_apps_json(path)
	for app in apps:
		get_app(app['url'], branch=app.get('branch'), bench_path=bench_path, build_asset_files=False)


def get_apps_json(path):
	if path.startswith('http'):
		r = requests.get(path)
		return r.json()
	else:
		with open(path) as f:
			return json.load(f)


def validate_branches():
	for app in ['frappe', 'erpnext']:
		branch = get_current_branch(app)

		if branch == "master":
			print('''
{}'s master branch has been renamed to version-11.

Please switch to the new branches to get future updates.

To switch to version 11, run the following command: `bench switch-to-branch version-11`
To switch to version 12, run the following command: `bench switch-to-branch version-12`
To switch to develop (unstable with experimental features), run the following command: `bench switch-to-branch develop`
			'''.format(app.title()))
			sys.exit(1)
