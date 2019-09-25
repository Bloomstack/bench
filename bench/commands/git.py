import re
import subprocess
from pathlib import Path

import click
import git

from bench.app import get_apps, get_app_dir
from bench.utils import set_git_remote_url


@click.command('remote-set-url')
@click.argument('git-url')
def remote_set_url(git_url):
	"Set app remote url"

	set_git_remote_url(git_url)


@click.command('remote-reset-url')
@click.argument('app')
def remote_reset_url(app):
	"Reset app remote url to frappe official"

	git_url = "https://github.com/frappe/{}.git".format(app)
	set_git_remote_url(git_url)


@click.command('remote-urls')
def remote_urls():
	"Show apps remote url"

	for app in get_apps():
		app_dir = get_app_dir(app)

		try:
			repo = git.Repo(app_dir)
		except git.exc.InvalidGitRepositoryError as e:
			continue

		remotes = [remote.name for remote in repo.remotes]

		print("{app}:".format(app=app))

		for remote in remotes:
			remote_url = repo.git.config("--get", 'remote.{}.url'.format(remote))
			print("\t{remote}: {remote_url}".format(remote=remote, remote_url=remote_url))
