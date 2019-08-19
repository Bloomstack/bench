import ast
import re

from setuptools import find_packages, setup

# get version from __version__ variable in bench/__init__.py
_version_re = re.compile(r'__version__\s+=\s+(.*)')

with open('requirements.txt') as f:
	install_requires = f.read().strip().split('\n')

with open('bench/__init__.py', 'rb') as f:
	version = str(ast.literal_eval(_version_re.search(
		f.read().decode('utf-8')).group(1)))

setup(
	name='bench',
	description='Metadata driven, full-stack web framework',
	author='Frappe Technologies',
	author_email='info@frappe.io',
	url='https://github.com/frappe/bench',
	version=version,
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires,
	python_requires='>=3.6, <4',
	classifiers=[
		"Development Status :: 5 - Production/Stable",
		"Environment :: Console",
		"Intended Audience :: Developers",
		"License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
		"Operating System :: MacOS",
		"Operating System :: Unix",
		"Programming Language :: Python :: 3.6",
		"Topic :: Utilities"
	],
	entry_points='''
[console_scripts]
bench=bench.cli:cli
''',
)
