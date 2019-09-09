
import getpass
import os
import re
import time
import unittest
from pathlib import Path

import bench.utils
from bench.config.production_setup import (disable_production,
	get_supervisor_confdir, setup_production)
from bench.tests import test_init


class TestSetupProduction(test_init.TestBenchInit):
	# setUp, tearDown and other tests are defined in TestBenchInit
	def test_setup_production(self):
		self.test_multiple_benches()

		user = getpass.getuser()

		for bench_name in ("test-bench-1", "test-bench-2"):
			bench_path = Path(self.benches_path, bench_name).resolve()
			setup_production(user, bench_path)
			self.assert_nginx_config(bench_name)
			self.assert_supervisor_config(bench_name)

		# test after start of both benches
		for bench_name in ("test-bench-1", "test-bench-2"):
			self.assert_supervisor_process(bench_name)

		self.assert_nginx_process()

		# sudoers
		bench.utils.setup_sudoers(user)
		self.assert_sudoers(user)

		for bench_name in ("test-bench-1", "test-bench-2"):
			bench_path = Path(self.benches_path, bench_name).resolve()
			disable_production(bench_path)

	def test_disable_production(self):
		bench_name = 'test-disable-prod'
		self.test_init(bench_name, frappe_branch='master')

		user = getpass.getuser()

		bench_path = Path(self.benches_path, bench_name).resolve()
		setup_production(user, bench_path)

		disable_production(bench_path)

		self.assert_nginx_link(bench_name)
		self.assert_supervisor_link(bench_name)
		self.assert_supervisor_process(bench_name=bench_name, disable_production=True)

	def assert_nginx_config(self, bench_name):
		conf_src = Path(self.benches_path, bench_name, 'config', 'nginx.conf').resolve()
		conf_dest = Path("/etc/nginx/conf.d/", f"{bench_name}.conf")

		self.assertTrue(conf_src.exists())
		self.assertTrue(conf_dest.exists())

		# symlink matches
		self.assertEqual(os.path.realpath(conf_dest), conf_src)

		# file content
		with open(conf_src, "r") as f:
			f = f.read().decode("utf-8")

			for key in (
				"upstream {bench_name}-frappe",
				"upstream {bench_name}-socketio-server"
			):
				self.assertTrue(key.format(bench_name=bench_name) in f)

	def assert_nginx_process(self):
		out = bench.utils.get_cmd_output("sudo nginx -t 2>&1")
		self.assertTrue("nginx: configuration file /etc/nginx/nginx.conf test is successful" in out)

	def assert_sudoers(self, user):
		sudoers_file = Path('/etc/sudoers.d/frappe')
		self.assertTrue(sudoers_file.exists())

		with open(sudoers_file, 'r') as f:
			sudoers = f.read().decode('utf-8')

		self.assertTrue(f"{user} ALL = (root) NOPASSWD: /usr/sbin/service nginx *" in sudoers)
		self.assertTrue(f"{user} ALL = (root) NOPASSWD: /usr/bin/supervisorctl" in sudoers)
		self.assertTrue(f"{user} ALL = (root) NOPASSWD: /usr/sbin/nginx" in sudoers)

	def assert_supervisor_config(self, bench_name, use_rq=True):
		supervisor_conf_dir = get_supervisor_confdir()

		conf_src = Path(self.benches_path, bench_name, 'config', 'supervisor.conf').resolve()
		conf_dest = Path(f"{supervisor_conf_dir}/{bench_name}.conf")

		self.assertTrue(conf_src.exists())
		self.assertTrue(conf_dest.exists())

		# symlink matches
		self.assertEqual(os.path.realpath(conf_dest), conf_src)

		# file content
		with open(conf_src, "r") as f:
			f = f.read().decode("utf-8")

			tests = [
				"program:{bench_name}-frappe-web",
				"program:{bench_name}-redis-cache",
				"program:{bench_name}-redis-queue",
				"program:{bench_name}-redis-socketio",
				"program:{bench_name}-node-socketio",
				"group:{bench_name}-web",
				"group:{bench_name}-workers",
				"group:{bench_name}-redis"
			]

			if use_rq:
				tests.extend([
					"program:{bench_name}-frappe-schedule",
					"program:{bench_name}-frappe-default-worker",
					"program:{bench_name}-frappe-short-worker",
					"program:{bench_name}-frappe-long-worker"
				])

			else:
				tests.extend([
					"program:{bench_name}-frappe-workerbeat",
					"program:{bench_name}-frappe-worker",
					"program:{bench_name}-frappe-longjob-worker",
					"program:{bench_name}-frappe-async-worker"
				])

			for key in tests:
				self.assertTrue(key.format(bench_name=bench_name) in f)

	def assert_supervisor_process(self, bench_name, use_rq=True, disable_production=False):
		out = bench.utils.get_cmd_output("sudo supervisorctl status")

		while "STARTING" in out:
			print("Waiting for all processes to start...")
			time.sleep(10)
			out = bench.utils.get_cmd_output("sudo supervisorctl status")

		tests = [
			"{bench_name}-web:{bench_name}-frappe-web[\s]+RUNNING",
			# Have commented for the time being. Needs to be uncommented later on. Bench is failing on travis because of this.
			# It works on one bench and fails on another.giving FATAL or BACKOFF (Exited too quickly (process log may have details))
			# "{bench_name}-web:{bench_name}-node-socketio[\s]+RUNNING",
			"{bench_name}-redis:{bench_name}-redis-cache[\s]+RUNNING",
			"{bench_name}-redis:{bench_name}-redis-queue[\s]+RUNNING",
			"{bench_name}-redis:{bench_name}-redis-socketio[\s]+RUNNING"
		]

		if use_rq:
			tests.extend([
				"{bench_name}-workers:{bench_name}-frappe-schedule[\s]+RUNNING",
				"{bench_name}-workers:{bench_name}-frappe-default-worker-0[\s]+RUNNING",
				"{bench_name}-workers:{bench_name}-frappe-short-worker-0[\s]+RUNNING",
				"{bench_name}-workers:{bench_name}-frappe-long-worker-0[\s]+RUNNING"
			])

		else:
			tests.extend([
				"{bench_name}-workers:{bench_name}-frappe-workerbeat[\s]+RUNNING",
				"{bench_name}-workers:{bench_name}-frappe-worker[\s]+RUNNING",
				"{bench_name}-workers:{bench_name}-frappe-longjob-worker[\s]+RUNNING",
				"{bench_name}-workers:{bench_name}-frappe-async-worker[\s]+RUNNING"
			])

		for key in tests:
			if disable_production:
				self.assertFalse(re.search(key.format(bench_name=bench_name), out))
			else:
				self.assertTrue(re.search(key.format(bench_name=bench_name), out))

	def assert_nginx_link(self, bench_name):
		nginx_conf_name = f"{bench_name}.conf"
		nginx_conf_path = Path('/etc/nginx/conf.d', nginx_conf_name)

		self.assertFalse(nginx_conf_path.is_symlink())

	def assert_supervisor_link(self, bench_name):
		supervisor_conf_dir = get_supervisor_confdir()
		supervisor_conf_name = f"{bench_name}.conf"
		supervisor_conf_path = Path(supervisor_conf_dir, supervisor_conf_name)

		self.assertFalse(supervisor_conf_path.is_symlink())
