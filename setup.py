import re
import sys

from setuptools import find_packages
from setuptools import setup


def get_version():
    version_re = r"^__version__\s+=\s+['\"]([^'\"]*)['\"]"
    version = None

    for line in open('yolo/__init__.py', 'r'):
        version_match = re.search(version_re, line, re.M)
        if version_match:
            version = version_match.group(1)
            break
    else:
        sys.exit('__version__ variable not found in yolo/__init__.py')

    return version


VERSION = get_version()

config = dict(
    name='yolo',
    version=VERSION,
    maintainer='Lars Butler',
    maintainer_email='lars.butler@rackspace.com',
    url='',
    description=(
        'Manage AWS infrastructure and microservices across multiple '
        'stages/accounts'
    ),
    packages=find_packages(exclude=['yolo.tests', 'yolo.tests.*']),
    entry_points={'console_scripts': ['yolo=yolo.script:cli']},
    install_requires=[
        'awscli',
        'boto3',
        'botocore',
        'click',
        'jinja2',
        'keyring',
        'requests',
        'ruamel.yaml',
        'tabulate',
        'voluptuous',
        'yoke',
    ],
)

setup(**config)
