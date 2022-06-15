# Copyright 2017 Rackspace US, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
    name='yolo-extension',
    version=VERSION,
    maintainer='Akshaya PS',
    maintainer_email='akshaya.ps@rackspace.com',
    url='',
    description=(
        'Manage AWS infrastructure and microservices across multiple '
        'stages/accounts'
    ),
    packages=find_packages(exclude=['yolo.tests', 'yolo.tests.*']),
    include_package_data=True,
    entry_points={'console_scripts': ['yolo=yolo.script:cli']},
    install_requires=[
        'awscli',
        'boto3',
        'botocore>=1.7.18',
        'click',
        'docker==3.4.0',
        'jinja2',
        'keyring==8.7.0',
        'keyrings.alt',
        'requests',
        'ruamel.yaml',
        'tabulate',
        'voluptuous',
    ],
    classifiers=[
        'Development Status :: 3 - Alpha',
        'License :: OSI Approved :: Apache Software License',
        'Intended Audience :: Developers',
        'Intended Audience :: Information Technology',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.8',
        'Topic :: Software Development :: Build Tools',
    ],
)

setup(**config)
