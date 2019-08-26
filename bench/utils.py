import glob
import grp
import hashlib
import itertools
import json
import logging
import multiprocessing
import os
import platform
import pwd
import select
import shutil
import subprocess
import sys
from distutils.spawn import find_executable
from pathlib import Path

import git
import requests
import semantic_version
from six import iteritems

import bench
from bench import env
from bench.exceptions import CommandFailedError, PatchError

logging.basicConfig(level=logging.INFO, format="(%(levelname)s) %(message)s")
logger = logging.getLogger(__name__)
folders_in_bench = ('apps', 'sites', 'config', 'logs', 'config/pids')


def safe_decode(string, encoding='utf-8'):
	try:
		string = string.decode(encoding)
	except Exception:
		pass
	return string


def get_frappe(bench_path='.'):
	frappe = get_env_cmd('frappe', bench_path)
	if not frappe.exists():
		print('frappe app is not installed. Run the following command to install frappe')
		print('bench get-app https://github.com/frappe/frappe.git')
	return frappe


def get_env_cmd(cmd, bench_path='.'):
	return Path(bench_path, 'env', 'bin', cmd).resolve()


def init(path, apps_path=None, no_procfile=False, no_backups=False,
		no_auto_update=False, frappe_path=None, frappe_branch=None, wheel_cache_dir=None,
		verbose=False, clone_from=None, skip_redis_config_generation=False,
		clone_without_update=False, ignore_exist=False, skip_assets=False,
		python='python3'):  # Let's change when we're ready. - <achilles@frappe.io>
	from bench.app import get_app, install_apps_from_path
	from bench.config.common_site_config import make_config
	from bench.config import redis
	from bench.config.procfile import setup_procfile
	from bench.patches import set_all_patches_executed

	import os.path as osp

	if osp.exists(path):
		if not ignore_exist:
			raise ValueError(f"Bench instance '{path}' already exists.")
	else:
		os.makedirs(path)

	for dirname in folders_in_bench:
		try:
			os.makedirs(os.path.join(path, dirname))
		except OSError as e:
			if e.errno == os.errno.EEXIST:
				pass

	setup_logging()
	setup_env(bench_path=path, python=python)
	make_config(path)

	if clone_from:
		clone_apps_from(bench_path=path, clone_from=clone_from, update_app=not clone_without_update)
	else:
		if not frappe_path:
			frappe_path = 'https://github.com/frappe/frappe.git'

		get_app(frappe_path, branch=frappe_branch, bench_path=path, build_asset_files=False, verbose=verbose)

		if apps_path:
			install_apps_from_path(apps_path, bench_path=path)

	bench.set_frappe_version(bench_path=path)
	if bench.FRAPPE_VERSION > 5 and not skip_assets:
		update_node_packages(bench_path=path)

	set_all_patches_executed(bench_path=path)
	if not skip_assets:
		build_assets(bench_path=path)

	if not skip_redis_config_generation:
		redis.generate_config(path)

	if not no_procfile:
		setup_procfile(path)
	if not no_backups:
		setup_backups(bench_path=path)
	if not no_auto_update:
		setup_auto_update(bench_path=path)
	copy_patches_txt(path)


def copy_patches_txt(bench_path):
	shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'patches', 'patches.txt'),
		os.path.join(bench_path, 'patches.txt'))


def clone_apps_from(bench_path, clone_from, update_app=True):
	from bench.app import install_app
	print(f"\nCopying apps from {clone_from}...")
	subprocess.check_output(['cp', '-R', os.path.join(clone_from, 'apps'), bench_path])

	node_modules_path = os.path.join(clone_from, 'node_modules')
	if os.path.exists(node_modules_path):
		print(f"\nCopying node_modules from {clone_from}...")
		subprocess.check_output(['cp', '-R', node_modules_path, bench_path])

	def setup_app(app):
		# run git reset --hard in each branch, pull latest updates and install_app
		app_path = os.path.join(bench_path, 'apps', app)

		# remove .egg-ino
		subprocess.check_output(['rm', '-rf', app + '.egg-info'], cwd=app_path)

		if update_app and os.path.exists(os.path.join(app_path, '.git')):
			remotes = subprocess.check_output(['git', 'remote'], cwd=app_path).strip().split()
			if 'upstream' in remotes:
				remote = 'upstream'
			else:
				remote = remotes[0]
			print(f"\nCleaning up {app}...")
			branch = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=app_path).strip()
			subprocess.check_output(['git', 'reset', '--hard'], cwd=app_path)
			subprocess.check_output(['git', 'pull', '--rebase', remote, branch], cwd=app_path)

		install_app(app, bench_path)

	with open(os.path.join(clone_from, 'sites', 'apps.txt'), 'r') as f:
		apps = f.read().splitlines()

	for app in apps:
		setup_app(app)


def exec_cmd(cmd, cwd='.'):
	from bench.cli import from_command_line

	is_async = False if from_command_line else True
	if is_async:
		stderr = stdout = subprocess.PIPE
	else:
		stderr = stdout = None

	logger.debug(cmd)

	p = subprocess.Popen(cmd, cwd=cwd, shell=True, stdout=stdout, stderr=stderr,
		universal_newlines=True)

	if is_async:
		return_code = print_output(p)
	else:
		return_code = p.wait()

	if return_code > 0:
		raise CommandFailedError(cmd)


def which(executable, raise_err=False):
	from distutils.spawn import find_executable
	exec_ = find_executable(executable)

	if not exec_ and raise_err:
		raise ValueError(f"{executable} not found.")

	return exec_


def setup_env(bench_path='.', python='python3'):
	python = which(python, raise_err=True)
	pip = Path('env', 'bin', 'pip')

	exec_cmd(f'virtualenv -q env -p {python}', cwd=bench_path)
	# exec_cmd(f'{pip} -q install --upgrade pip', cwd=bench_path)
	exec_cmd(f'{pip} -q install wheel', cwd=bench_path)
	exec_cmd(f'{pip} -q install six', cwd=bench_path)
	exec_cmd(f'{pip} -q install -e git+https://github.com/frappe/python-pdfkit.git#egg=pdfkit', cwd=bench_path)


def setup_socketio(bench_path='.'):
	exec_cmd("npm install socket.io redis express superagent cookie babel-core less chokidar \
		babel-cli babel-preset-es2015 babel-preset-es2016 babel-preset-es2017 babel-preset-babili", cwd=bench_path)


def patch_sites(bench_path='.'):
	try:
		run_frappe_cmd('--site', 'all', 'migrate', bench_path=bench_path)
	except subprocess.CalledProcessError:
		raise PatchError


def build_assets(bench_path='.', app=None):
	if app:
		exec_cmd(f'bench build --app {app}', cwd=bench_path)
	else:
		exec_cmd('bench build', cwd=bench_path)


def get_sites(bench_path='.'):
	sites_dir = Path(bench_path, "sites")
	sites = [site.name for site in sites_dir.iterdir() if site.is_dir() and site.name not in ('assets',)]
	return sites


def get_sites_dir(bench_path='.'):
	return os.path.abspath(os.path.join(bench_path, 'sites'))


def get_bench_dir(bench_path='.'):
	bench_path = Path(bench_path)
	return bench_path.resolve()


def setup_auto_update(bench_path='.'):
	logger.info('Setting up auto update')
	bench_dir = get_bench_dir(bench_path)
	add_to_crontab('0 10 * * * cd {bench_dir} &&  {bench} update --auto >> {logfile} 2>&1'.format(
		bench_dir=bench_dir,
		bench=Path(bench_dir, 'env', 'bin', 'bench'),
		logfile=Path(bench_dir, 'logs', 'auto_update_log.log')
	))


def setup_backups(bench_path='.'):
	logger.info('Setting up backups')
	bench_dir = get_bench_dir(bench_path)
	backup_command = f"cd {bench_dir} && {sys.argv[0]} --site all backup"

	add_to_crontab('0 */6 * * *  {backup_command} >> {logfile} 2>&1'.format(
		backup_command=backup_command,
		logfile=Path(bench_dir, 'logs', 'backup.log')
	))


def add_to_crontab(line):
	current_crontab = read_crontab()
	line = str.encode(line)
	if not line in current_crontab:
		cmd = ["crontab"]
		if platform.system() == 'FreeBSD' or platform.linux_distribution()[0]=="arch":
			cmd = ["crontab", "-"]
		s = subprocess.Popen(cmd, stdin=subprocess.PIPE)
		s.stdin.write(current_crontab)
		s.stdin.write(line + b'\n')
		s.stdin.close()


def read_crontab():
	s = subprocess.Popen(["crontab", "-l"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
	out = s.stdout.read()
	s.stdout.close()
	return out


def update_bench():
	print('\nUpdating bench...')
	BENCH_DIR = Path(__file__).resolve().parent.parent
	bench_repo = git.Repo(BENCH_DIR)

	# define bench repo's remote and branch
	remote = "upstream"
	branch = "master"
	commit_count = get_commits_to_pull(BENCH_DIR, remote, branch)

	if commit_count > 0:
		bench_repo.git.pull(remote, branch)
		print('...done')
	else:
		print('...already up-to-date')


def get_commits_to_pull(app_dir, remote="upstream", branch="master"):
	try:
		bench_repo = git.Repo(app_dir)
	except git.exc.InvalidGitRepositoryError as e:
		return 0

	current_branch = bench_repo.active_branch.name
	commits_to_pull = bench_repo.iter_commits(f"{current_branch}..{remote}/{branch}")
	commit_count = sum([1 for commit in commits_to_pull])

	return commit_count


def setup_sudoers(user):
	if not os.path.exists('/etc/sudoers.d'):
		os.makedirs('/etc/sudoers.d')

		set_permissions = False
		if not os.path.exists('/etc/sudoers'):
			set_permissions = True

		with open('/etc/sudoers', 'a') as f:
			f.write('\n#includedir /etc/sudoers.d\n')

		if set_permissions:
			os.chmod('/etc/sudoers', 0o440)

	sudoers_file = '/etc/sudoers.d/frappe'

	template = env.get_template('frappe_sudoers')
	frappe_sudoers = template.render(**{
		'user': user,
		'service': find_executable('service'),
		'systemctl': find_executable('systemctl'),
		'supervisorctl': find_executable('supervisorctl'),
		'nginx': find_executable('nginx'),
		'bench': find_executable('bench')
	})
	frappe_sudoers = safe_decode(frappe_sudoers)

	with open(sudoers_file, 'w') as f:
		f.write(frappe_sudoers)

	os.chmod(sudoers_file, 0o440)


def setup_logging(bench_path='.'):
	if os.path.exists(os.path.join(bench_path, 'logs')):
		logger = logging.getLogger('bench')
		log_file = os.path.join(bench_path, 'logs', 'bench.log')
		formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
		hdlr = logging.FileHandler(log_file)
		hdlr.setFormatter(formatter)
		logger.addHandler(hdlr)
		logger.setLevel(logging.DEBUG)


def get_program(programs):
	program = None
	for p in programs:
		program = find_executable(p)
		if program:
			break
	return program


def get_process_manager():
	return get_program(['foreman', 'forego', 'honcho'])

def start(no_dev=False, concurrency=None, procfile=None):
	program = get_process_manager()
	if not program:
		raise Exception("No process manager found")
	os.environ['PYTHONUNBUFFERED'] = "true"
	if not no_dev:
		os.environ['DEV_SERVER'] = "true"

	command = [program, 'start']
	if concurrency:
		command.extend(['-c', concurrency])

	if procfile:
		command.extend(['-f', procfile])

	os.execv(program, command)


def check_cmd(cmd, cwd='.'):
	try:
		subprocess.check_call(cmd, cwd=cwd, shell=True)
		return True
	except subprocess.CalledProcessError:
		return False


def get_git_version():
	'''returns git version from `git --version`
	extracts version number from string `get version 1.9.1` etc'''
	version = get_cmd_output("git --version")
	version = safe_decode(version)
	version = version.strip().split()[2]
	version = '.'.join(version.split('.')[0:2])
	return float(version)


def check_git_for_shallow_clone():
	from bench.config.common_site_config import get_config
	config = get_config('.')

	if config:
		if config.get('release_bench'):
			return False

		if not config.get('shallow_clone'):
			return False

	git_version = get_git_version()
	if git_version > 1.9:
		return True


def get_cmd_output(cmd, cwd='.'):
	try:
		output = subprocess.check_output(cmd, cwd=cwd, shell=True, stderr=subprocess.PIPE).strip()
		output = output.decode('utf-8')
		return output
	except subprocess.CalledProcessError as e:
		if e.output:
			print(e.output)
		raise


def safe_encode(what, encoding='utf-8'):
	try:
		what = what.encode(encoding)
	except Exception:
		pass

	return what


def restart_supervisor_processes(bench_path='.', web_workers=False):
	from bench.config.common_site_config import get_config
	conf = get_config(bench_path=bench_path)
	bench_name = get_bench_name(bench_path)

	cmd = conf.get('supervisor_restart_cmd')
	if cmd:
		exec_cmd(cmd, cwd=bench_path)

	else:
		supervisor_status = subprocess.check_output(['sudo', 'supervisorctl', 'status'], cwd=bench_path)
		supervisor_status = safe_decode(supervisor_status)

		if web_workers and f"{bench_name}-web:" in supervisor_status:
			group = f"{bench_name}-web:	"
		elif f"{bench_name}-workers:" in supervisor_status:
			group = f"{bench_name}-workers: {bench_name}-web:"
		# backward compatibility
		elif f"{bench_name}-processes:" in supervisor_status:
			group = f"{bench_name}-processes:"
		# backward compatibility
		else:
			group = 'frappe:'

		exec_cmd(f"sudo supervisorctl restart {group}", cwd=bench_path)


def restart_systemd_processes(bench_path='.', web_workers=False):
	bench_name = get_bench_name(bench_path)
	exec_cmd(f"sudo systemctl stop -- $(systemctl show -p Requires {bench_name}.target | cut -d= -f2)")
	exec_cmd(f"sudo systemctl start -- $(systemctl show -p Requires {bench_name}.target | cut -d= -f2)")


def set_default_site(site, bench_path='.'):
	if not site in get_sites(bench_path):
		raise Exception("Site not in bench")

	exec_cmd(f"{get_frappe(bench_path)} --use {site}", cwd=os.path.join(bench_path, 'sites'))


def update_requirements(bench_path='.'):
	print('\nUpdating python requirements...')
	pip = get_env_cmd('pip', bench_path)

	# Update pip
	exec_cmd(f"{pip} install --upgrade pip")

	# Update bench requirements
	bench_req_file = Path(bench.__path__[0]).with_name('requirements.txt')
	if bench_req_file.exists():
		install_requirements(pip, bench_req_file)
	print('...done')


def reinstall_apps(bench_path='.'):
	print('\nRe-installing apps...')
	from bench.app import get_apps, install_app

	for app in get_apps():
		install_app(app, bench_path)
	print('...done')


def update_node_packages(bench_path='.'):
	from bench.app import get_develop_version
	from distutils.version import LooseVersion
	v = LooseVersion(get_develop_version('frappe', bench_path))

	# After rollup was merged, frappe_version = 10.1
	# if develop_verion is 11 and up, only then install yarn
	if v < LooseVersion('11.x.x-develop'):
		print('\nUpdating npm packages...')
		update_npm_packages(bench_path)
	else:
		print('\nUpdating yarn packages...')
		update_yarn_packages(bench_path)
	print('...done')


def update_yarn_packages(bench_path='.'):
	apps_dir = Path(bench_path, 'apps')

	if not find_executable('yarn'):
		print("Please install yarn using below command and try again.")
		print("`npm install -g yarn`")
		return

	for app in apps_dir.iterdir():
		app_package_json = app.joinpath('package.json')
		if app_package_json.exists():
			print(f"...{app.name} packages...")
			exec_cmd('yarn install', cwd=app)


def update_npm_packages(bench_path='.'):
	apps_dir = Path(bench_path, 'apps')
	package_json = {}

	for app in apps_dir.iterdir():
		package_json_path = app.joinpath('package.json')

		if package_json_path.exists():
			app_package_json = json.loads(package_json_path.read_text())
			# package.json is usually a dict in a dict
			for key, value in iteritems(app_package_json):
				if not key in package_json:
					package_json[key] = value
				else:
					if isinstance(value, dict):
						package_json[key].update(value)
					elif isinstance(value, list):
						package_json[key].extend(value)
					else:
						package_json[key] = value

	if not package_json:
		package_json_file = Path(__file__).parent.joinpath('package.json')
		package_json = json.loads(package_json_file.read_text())

	bench_package_json = Path(bench_path, 'package.json')
	bench_package_json.write_text(json.dumps(package_json, indent=1, sort_keys=True))

	exec_cmd('npm install', cwd=bench_path)


def install_requirements(pip, req_file):
	exec_cmd(f"{pip} install -q -r {req_file}")


def backup_site(site, bench_path='.'):
	run_frappe_cmd('--site', site, 'backup', bench_path=bench_path)


def backup_all_sites(bench_path='.'):
	for site in get_sites(bench_path):
		backup_site(site, bench_path)


def is_root():
	if os.getuid() == 0:
		return True
	return False


def set_mariadb_host(host, bench_path='.'):
	update_common_site_config({'db_host': host}, bench_path=bench_path)


def set_redis_cache_host(host, bench_path='.'):
	update_common_site_config({'redis_cache': "redis://{}".format(host)}, bench_path=bench_path)


def set_redis_queue_host(host, bench_path='.'):
	update_common_site_config({'redis_queue': "redis://{}".format(host)}, bench_path=bench_path)


def set_redis_socketio_host(host, bench_path='.'):
	update_common_site_config({'redis_socketio': "redis://{}".format(host)}, bench_path=bench_path)


def update_common_site_config(ddict, bench_path='.'):
	update_json_file(os.path.join(bench_path, 'sites', 'common_site_config.json'), ddict)


def update_json_file(filename, ddict):
	if os.path.exists(filename):
		with open(filename, 'r') as f:
			content = json.load(f)

	else:
		content = {}

	content.update(ddict)
	with open(filename, 'w') as f:
		json.dump(content, f, indent=1, sort_keys=True)


def drop_privileges(uid_name='nobody', gid_name='nogroup'):
	# from http://stackoverflow.com/a/2699996
	if os.getuid() != 0:
		# We're not root so, like, whatever dude
		return

	# Get the uid/gid from the name
	running_uid = pwd.getpwnam(uid_name).pw_uid
	running_gid = grp.getgrnam(gid_name).gr_gid

	# Remove group privileges
	os.setgroups([])

	# Try setting the new uid/gid
	os.setgid(running_gid)
	os.setuid(running_uid)

	# Ensure a very conservative umask
	os.umask(0o22)


def fix_prod_setup_perms(bench_path='.', frappe_user=None):
	from bench.config.common_site_config import get_config

	if not frappe_user:
		frappe_user = get_config(bench_path).get('frappe_user')

	if not frappe_user:
		print("frappe user not set")
		sys.exit(1)

	globs = ["logs/*", "config/*"]
	for glob_name in globs:
		for path in glob.glob(glob_name):
			uid = pwd.getpwnam(frappe_user).pw_uid
			gid = grp.getgrnam(frappe_user).gr_gid
			os.chown(path, uid, gid)


def fix_file_perms():
	for dir_path, dirs, files in os.walk('.'):
		for _dir in dirs:
			os.chmod(os.path.join(dir_path, _dir), 0o755)
		for _file in files:
			os.chmod(os.path.join(dir_path, _file), 0o644)
	bin_dir = './env/bin'
	if os.path.exists(bin_dir):
		for _file in os.listdir(bin_dir):
			if not _file.startswith('activate'):
				os.chmod(os.path.join(bin_dir, _file), 0o755)


def get_current_frappe_version(bench_path='.'):
	from bench.app import get_current_frappe_version as fv
	return fv(bench_path=bench_path)


def run_frappe_cmd(*args, **kwargs):
	from bench.cli import from_command_line

	bench_path = kwargs.get('bench_path', '.')
	f = get_env_cmd('python', bench_path)
	sites_dir = Path(bench_path, 'sites')

	is_async = False if from_command_line else True
	stderr = stdout = subprocess.PIPE if is_async else None

	p = subprocess.Popen((f, '-m', 'frappe.utils.bench_helper', 'frappe') + args, cwd=sites_dir, stdout=stdout, stderr=stderr)

	return_code = print_output(p) if is_async else p.wait()
	if return_code > 0:
		sys.exit(return_code)


def get_frappe_cmd_output(*args, **kwargs):
	bench_path = kwargs.get('bench_path', '.')
	f = get_env_cmd('python', bench_path)
	sites_dir = Path(bench_path, 'sites')
	return subprocess.check_output((f, '-m', 'frappe.utils.bench_helper', 'frappe') + args, cwd=sites_dir)


def validate_upgrade(from_ver, to_ver, bench_path='.'):
	if to_ver >= 6:
		if not find_executable('npm') and not (find_executable('node') or find_executable('nodejs')):
			raise Exception("Please install nodejs and npm")


def post_upgrade(from_ver, to_ver, bench_path='.'):
	from bench.config.common_site_config import get_config
	from bench.config import redis
	from bench.config.supervisor import generate_supervisor_config
	from bench.config.nginx import make_nginx_conf
	conf = get_config(bench_path=bench_path)
	print("-"*80)
	print(f"Your apps were upgraded from {from_ver} to {to_ver}")

	if conf.get('restart_supervisor_on_update'):
		redis.generate_config(bench_path=bench_path)
		generate_supervisor_config(bench_path=bench_path)
		make_nginx_conf(bench_path=bench_path)

		print("As you have setup your bench for production, you will have to reload configuration for nginx and supervisor")
		print("To complete the migration, please run the following commands")
		print()
		print("sudo service nginx restart")
		print("sudo supervisorctl reload")


def update_translations_p(args):
	try:
		update_translations(*args)
	except requests.exceptions.HTTPError:
		print('Download failed for', args[0], args[1])


def download_translations_p():
	pool = multiprocessing.Pool(4)

	langs = get_langs()
	apps = ('frappe', 'erpnext')
	args = list(itertools.product(apps, langs))

	pool.map(update_translations_p, args)


def download_translations():
	langs = get_langs()
	apps = ('frappe', 'erpnext')
	for app, lang in itertools.product(apps, langs):
		update_translations(app, lang)


def get_langs():
	lang_file = 'apps/frappe/frappe/geo/languages.json'
	with open(lang_file) as f:
		langs = json.loads(f.read())
	return [d['code'] for d in langs]


def update_translations(app, lang):
	translations_dir = os.path.join('apps', app, app, 'translations')
	csv_file = os.path.join(translations_dir, lang + '.csv')
	url = f"https://translate.erpnext.com/files/{app}-{lang}.csv"
	r = requests.get(url, stream=True)
	r.raise_for_status()

	with open(csv_file, 'wb') as f:
		for chunk in r.iter_content(chunk_size=1024):
			# filter out keep-alive new chunks
			if chunk:
				f.write(chunk)
				f.flush()

	print('downloaded for', app, lang)


def print_output(p):
	while p.poll() is None:
		readx = select.select([p.stdout.fileno(), p.stderr.fileno()], [], [])[0]
		send_buffer = []
		for fd in readx:
			if fd == p.stdout.fileno():
				while 1:
					buf = p.stdout.read(1)
					if not len(buf):
						break
					if buf == '\r' or buf == '\n':
						send_buffer.append(buf)
						log_line(''.join(send_buffer), 'stdout')
						send_buffer = []
					else:
						send_buffer.append(buf)

			if fd == p.stderr.fileno():
				log_line(p.stderr.readline(), 'stderr')
	return p.poll()


def log_line(data, stream):
	if stream == 'stderr':
		return sys.stderr.write(data)
	return sys.stdout.write(data)


def get_output(*cmd):
	s = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
	out = s.stdout.read()
	s.stdout.close()
	return out


def before_update(bench_path, requirements):
	validate_pillow_dependencies(bench_path, requirements)


def validate_pillow_dependencies(bench_path, requirements):
	if not requirements:
		return

	print("\nValidate Pillow dependencies...")
	try:
		pip = Path(bench_path, 'env', 'bin', 'pip')
		exec_cmd(f"{pip} install Pillow")
	except CommandFailedError:
		distname, version, id = platform.linux_distribution()
		distro_name = distname.lower()
		if any([dist in distro_name for dist in ["centos", "fedora"]]):
			print("Please install these dependencies using the command:")
			print("sudo yum install libtiff-devel libjpeg-devel libzip-devel freetype-devel lcms2-devel libwebp-devel tcl-devel tk-devel")
			raise
		elif any([dist in distro_name for dist in ["ubuntu", "elementary os", "debian"]]):
			print("Please install these dependencies using the command:")
			if "ubuntu" in distro_name and version == "12.04":
				print("sudo apt-get install -y libtiff4-dev libjpeg8-dev zlib1g-dev libfreetype6-dev liblcms2-dev libwebp-dev tcl8.5-dev tk8.5-dev python-tk")
			else:
				print("sudo apt-get install -y libtiff5-dev libjpeg8-dev zlib1g-dev libfreetype6-dev liblcms2-dev libwebp-dev tcl8.6-dev tk8.6-dev python-tk")
			raise
	print("...done")


def get_bench_name(bench_path):
	return os.path.basename(os.path.abspath(bench_path))


def setup_fonts():
	fonts_path = os.path.join('/tmp', 'fonts')

	if os.path.exists('/etc/fonts_backup'):
		return

	exec_cmd("git clone https://github.com/frappe/fonts.git", cwd='/tmp')
	os.rename('/etc/fonts', '/etc/fonts_backup')
	os.rename('/usr/share/fonts', '/usr/share/fonts_backup')
	os.rename(os.path.join(fonts_path, 'etc_fonts'), '/etc/fonts')
	os.rename(os.path.join(fonts_path, 'usr_share_fonts'), '/usr/share/fonts')
	shutil.rmtree(fonts_path)
	exec_cmd("fc-cache -fv")


def set_git_remote_url(git_url, bench_path='.'):
	"Set app remote git url"
	app = git_url.rsplit('/', 1)[1].rsplit('.', 1)[0]

	if app not in bench.app.get_apps(bench_path):
		print(f"No app named {app}")
		sys.exit(1)

	app_dir = bench.app.get_repo_dir(app, bench_path)
	if Path(app_dir, '.git').exists():
		exec_cmd(f"git remote set-url upstream {git_url}", cwd=app_dir)


def run_playbook(playbook_name, extra_vars=None, tag=None):
	if not find_executable('ansible'):
		print("Ansible is needed to run this command, please install it using 'pip install ansible'")
		sys.exit(1)
	args = ['ansible-playbook', '-c', 'local', playbook_name, '-vvvv']

	if extra_vars:
		args.extend(['-e', json.dumps(extra_vars)])

	if tag:
		args.extend(['-t', tag])

	subprocess.check_call(args, cwd=os.path.join(os.path.dirname(bench.__path__[0]), 'playbooks'))
