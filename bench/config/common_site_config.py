import getpass
import json
import multiprocessing
from pathlib import Path

try:
	from urllib.parse import urlparse
except ImportError:
	from urlparse import urlparse

DEFAULT_CONFIG = {
	'auto_update': False,
	'background_workers': 1,
	'frappe_user': getpass.getuser(),
	'gunicorn_workers': multiprocessing.cpu_count(),
	'rebase_on_pull': False,
	'restart_supervisor_on_update': False,
	'restart_systemd_on_update': False,
	'serve_default_site': True,
	'shallow_clone': True,
	'update_bench_on_update': True
}

DEFAULT_PORTS = {
	"webserver_port": 8000,
	"socketio_port": 9000,
	"file_watcher_port": 6787,
	"redis_queue": 11000,
	"redis_socketio": 12000,
	"redis_cache": 13000
}


def make_config(bench_path):
	make_pid_folder(bench_path)
	bench_config = get_config(bench_path)
	bench_config.update(DEFAULT_CONFIG)
	update_config_for_frappe(bench_config, bench_path)
	put_config(bench_config, bench_path)


def get_config(bench_path):
	return get_common_site_config(bench_path)


def get_common_site_config(bench_path):
	config_file = get_config_path(bench_path)
	if not config_file.exists():
		return {}
	return json.loads(config_file.read_text())


def put_config(config, bench_path='.'):
	config_file = get_config_path(bench_path)
	config_file.write_text(json.dumps(config, indent=1, sort_keys=True))


def update_config(new_config, bench_path='.'):
	config = get_config(bench_path)
	config.update(new_config)
	put_config(config, bench_path)


def get_config_path(bench_path):
	return Path(bench_path, "sites", "common_site_config.json")


def get_gunicorn_workers():
	'''
		This function will return the maximum workers that can be started,
		depending upon the number of CPUs present on the machine
	'''

	return {
		"gunicorn_workers": multiprocessing.cpu_count()
	}


def update_config_for_frappe(config, bench_path):
	ports = make_ports(bench_path)

	for key in ('redis_cache', 'redis_queue', 'redis_socketio'):
		if key not in config:
			config[key] = "redis://localhost:{0}".format(ports[key])

	for key in ('webserver_port', 'socketio_port', 'file_watcher_port'):
		if key not in config:
			config[key] = ports[key]

	# TODO: Optionally we need to add the host or domain name in case dns_multitenant is false


def make_ports(bench_path):
	# collect all existing ports
	existing_ports = {}
	benches_dir = Path(bench_path).resolve().parent
	benches = [d for d in benches_dir.iterdir() if d.is_dir()]

	for bench in benches:
		bench_config = get_config(bench)
		for key in DEFAULT_PORTS.keys():
			value = bench_config.get(key)

			# extract port from redis url
			if value and (key in ('redis_cache', 'redis_queue', 'redis_socketio')):
				value = urlparse(value).port

			if value:
				existing_ports.setdefault(key, []).append(value)

	# new port value = max of existing port value + 1
	ports = {}
	for key, value in DEFAULT_PORTS.items():
		existing_value = existing_ports.get(key, [])
		if existing_value:
			value = max(existing_value) + 1

		ports[key] = value

	return ports


def make_pid_folder(bench_path):
	pids_path = Path(bench_path, "config", "pids")
	pids_path.mkdir(parents=True, exist_ok=True)
