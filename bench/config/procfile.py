import os
from pathlib import Path

import click

import bench
from bench.app import use_rq
from bench.config.common_site_config import get_config
from bench.utils import find_executable


def setup_procfile(bench_path, yes=False):
	config = get_config(bench_path)
	procfile_path = Path(bench_path, 'Procfile')

	if not yes and procfile_path.exists():
		click.confirm('A Procfile already exists and this will overwrite it. Do you want to continue?', abort=True)

	procfile = bench.env.get_template('Procfile').render(
		node=find_executable("node") or find_executable("nodejs"),
		use_rq=use_rq(bench_path),
		webserver_port=config.get('webserver_port'),
		CI=os.environ.get('CI'))

	procfile_path.write_text(procfile)
