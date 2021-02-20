"""Module for setting up system and respective bench configurations"""

# imports - third party imports

def env():
	from jinja2 import Environment, PackageLoader
	return Environment(loader=PackageLoader('bench.config'))
