import errno
import os
from pathlib import Path

import click
from crontab import CronTab

import bench
from bench.config.common_site_config import get_config
from bench.config.nginx import make_nginx_conf
from bench.config.production_setup import service
from bench.config.site_config import get_domains, remove_domain, update_site_config
from bench.exceptions import CommandFailedError
from bench.utils import exec_cmd, update_common_site_config

try:
	from urllib.request import urlretrieve
except ImportError:
	from urllib import urlretrieve


def setup_letsencrypt(site, custom_domain, bench_path, interactive):

	site_path = Path(bench_path, "sites", site, "site_config.json")
	if not site_path.parent.exists():
		print(f"No site named '{site}'")
		return

	if custom_domain:
		domains = get_domains(site, bench_path)
		for d in domains:
			if (isinstance(d, dict) and d['domain'] == custom_domain):
				print(f"SSL for Domain {custom_domain} already exists")
				return

		if custom_domain not in domains:
			print(f"No custom domain named {custom_domain} set for site")
			return

	click.confirm('Running this will stop the nginx service temporarily causing your sites to go offline\n'
		'Do you want to continue?',
		abort=True)

	if not get_config(bench_path).get("dns_multitenant"):
		print("You cannot setup SSL without DNS Multitenancy")
		return

	create_config(site, custom_domain)
	run_certbot_and_setup_ssl(site, custom_domain, bench_path, interactive)
	setup_crontab()


def create_config(site, custom_domain):
	config = bench.env.get_template('letsencrypt.cfg').render(domain=custom_domain or site)
	config_path = f'/etc/letsencrypt/configs/{custom_domain or site}.cfg'
	create_dir_if_missing(config_path)

	with open(config_path, 'w') as f:
		f.write(config)


def run_certbot_and_setup_ssl(site, custom_domain, bench_path, interactive=True):
	service('nginx', 'stop')
	get_certbot()
	path = get_certbot_path()

	try:
		interactive = '' if interactive else '-n'
		exec_cmd(f"{path} {interactive} --config /etc/letsencrypt/configs/{custom_domain or site}.cfg certonly")
	except CommandFailedError:
		service('nginx', 'start')
		print("There was a problem trying to setup SSL for your site")
		return

	ssl_path = f"/etc/letsencrypt/live/{custom_domain or site}/"
	ssl_config = {"ssl_certificate": Path(ssl_path, "fullchain.pem"), "ssl_certificate_key": Path(ssl_path, "privkey.pem")}

	if custom_domain:
		remove_domain(site, custom_domain, bench_path)
		domains = get_domains(site, bench_path)
		ssl_config['domain'] = custom_domain
		domains.append(ssl_config)
		update_site_config(site, {"domains": domains}, bench_path=bench_path)
	else:
		update_site_config(site, ssl_config, bench_path=bench_path)

	make_nginx_conf(bench_path)
	service('nginx', 'start')


def setup_crontab():
	job_command = 'sudo service nginx stop && /opt/certbot-auto renew && sudo service nginx start'
	system_crontab = CronTab(tabfile='/etc/crontab', user=True)
	if job_command not in str(system_crontab):
		job = system_crontab.new(command=job_command, comment="Renew lets-encrypt every month")
		job.every().month()
		job.enable()
		system_crontab.write()


def create_dir_if_missing(path):
	if not os.path.exists(os.path.dirname(path)):
		os.makedirs(os.path.dirname(path))


def get_certbot():
	certbot_path = get_certbot_path()
	create_dir_if_missing(certbot_path)

	if not os.path.isfile(certbot_path):
		urlretrieve("https://dl.eff.org/certbot-auto", certbot_path)
		os.chmod(certbot_path, 0o744)


def get_certbot_path():
	return "/opt/certbot-auto"


def renew_certs():
	click.confirm('Running this will stop the nginx service temporarily causing your sites to go offline\n'
		'Do you want to continue?',
		abort=True)

	path = get_certbot_path()
	service('nginx', 'stop')
	exec_cmd(f"{path} renew")
	service('nginx', 'start')


def setup_wildcard_ssl(domain, email, bench_path, exclude_base_domain):

	def _get_domains(domain):
		domain_list = [domain]

		if not domain.startswith('*.'):
			# add wildcard caracter to domain if missing
			domain_list.append(f"*.{domain}")
		else:
			# include base domain based on flag
			domain_list.append(domain.replace('*.', ''))

		if exclude_base_domain:
			domain_list.remove(domain.replace('*.', ''))

		return domain_list

	if not get_config(bench_path).get("dns_multitenant"):
		print("You cannot setup SSL without DNS Multitenancy")
		return

	get_certbot()
	path = get_certbot_path()
	domain_list = _get_domains(domain.strip())

	email_param = ''
	if email:
		email_param = f"--email {email}"

	try:
		exec_cmd(f"{path} certonly --manual --preferred-challenges=dns {email_param} \
			--server https://acme-v02.api.letsencrypt.org/directory \
			--agree-tos -d {' -d '.join(domain_list)}")

	except CommandFailedError:
		print("There was a problem trying to setup SSL")
		return

	ssl_path = f"/etc/letsencrypt/live/{domain}/"
	ssl_config = {
		"wildcard": {
			"domain": domain,
			"ssl_certificate": Path(ssl_path, "fullchain.pem"),
			"ssl_certificate_key": Path(ssl_path, "privkey.pem")
		}
	}

	update_common_site_config(ssl_config)
	setup_crontab()

	make_nginx_conf(bench_path)
	print("Restrting Nginx service")
	service('nginx', 'restart')
