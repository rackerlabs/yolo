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

from collections import namedtuple
import sys

import jinja2
from ruamel import yaml
import voluptuous as volup

import yolo.exceptions
from yolo import utils

PY3 = sys.version_info >= (2, 8)
if PY3:
    unicode = str

STRING_SCHEMA = volup.Any(str, unicode)
STRING_OR_DICT_SCHEMA = volup.Any(str, unicode, dict)

AWSAccount = namedtuple(
    'AWSAccount', ['name', 'account_number', 'default_region']
)


class YoloFile(object):
    """Object representation of a yolo.yaml file."""
    DEFAULT_STAGE = 'default'

    # 'accounts' section
    ACCOUNT_SCHEMA = volup.Schema({
        volup.Required('name'): STRING_SCHEMA,
        volup.Required('account_number'): STRING_SCHEMA,
        volup.Required('default_region'): STRING_SCHEMA,
    })
    ACCOUNTS_SCHEMA = volup.Schema([ACCOUNT_SCHEMA])
    # 'templates' section
    ACCOUNT_TEMPLATE_SCHEMA = volup.Schema({
        volup.Required('path'): STRING_SCHEMA,
        volup.Optional('params'): {STRING_SCHEMA: STRING_SCHEMA},
    })
    STAGE_TEMPLATE_SCHEMA = volup.Schema({
        volup.Required('path'): STRING_SCHEMA,
        volup.Optional('params'): {STRING_SCHEMA: STRING_SCHEMA},
    })
    TEMPLATES_SCHEMA = volup.Schema({
        volup.Optional('account'): ACCOUNT_TEMPLATE_SCHEMA,
        volup.Optional('stage'): STAGE_TEMPLATE_SCHEMA,
    })
    # 'stages' section
    STAGES_SCHEMA = volup.Schema({
        STRING_SCHEMA: {  # stage name, arbitrary string
            volup.Required('account'): STRING_SCHEMA,
            volup.Required('region'): STRING_SCHEMA,
            volup.Optional('protected'): bool,
            volup.Optional('params'): {STRING_SCHEMA: STRING_SCHEMA},
        },
    })
    # 'services' section
    PARAMETERS_SCHEMA = volup.Schema({
        # Key is the stage name.
        # Config items are defined as a list of dicts.
        volup.Required('stages'): {
            # The key here is the stage name.
            STRING_SCHEMA: [{
                # Parameter name:
                volup.Required('name'): STRING_SCHEMA,
                # Specify a value inline if the value isn't secret/sensitive.
                volup.Optional('value'): STRING_SCHEMA,
                # Indicate if the value should be collected as a multiline string,
                # such as in the case of a certificate or private key block.
                volup.Optional('multiline', default=False): bool,
            }],
        },
    })
    SUPPORTED_RUNTIMES = [
        'python2.7',
        'python3.6',
        'python3.8'
    ]
    YOKE_LAMBDA_FN_CFG = volup.Schema({
        volup.Required('FunctionName'): STRING_SCHEMA,
        volup.Required('Role'): STRING_SCHEMA,
        volup.Required('Handler'): STRING_SCHEMA,
        volup.Optional('Description'): STRING_SCHEMA,
        volup.Optional('Timeout'): int,
        volup.Optional('MemorySize'): int,
        volup.Optional('VpcConfig'): {
            volup.Required('SubnetIds'): [str],
            volup.Required('SecurityGroupIds'): [str],
        },
        volup.Optional('Environment'): {
            'Variables': {STRING_SCHEMA: STRING_SCHEMA},
        },
        volup.Optional('Runtime'): volup.Any(*SUPPORTED_RUNTIMES),
        volup.Optional('TracingConfig'): {
            volup.Required('Mode'): volup.Any('Active', 'PassThrough'),
        },
    })
    YOKE_SCHEMA = volup.Schema({
        # value is a dictionary of strings or dicts, keyed by strings
        volup.Required('environment'): {
            STRING_SCHEMA: STRING_OR_DICT_SCHEMA,
        },
        volup.Optional('working_dir'): STRING_SCHEMA,
        volup.Optional('stage'): STRING_SCHEMA,
    })
    APIGATEWAY_SCHEMA = volup.Schema({
        volup.Required('rest_api_name'): STRING_SCHEMA,
        volup.Required('swagger_template'): STRING_SCHEMA,

        volup.Optional('domains'): [{
            volup.Optional('domain_name'): STRING_OR_DICT_SCHEMA,
            volup.Optional('base_path'): STRING_SCHEMA,
        }],

        volup.Optional('authorizers'): [{
            volup.Required('name'): STRING_SCHEMA,
            volup.Required('type'): volup.Any(
                'TOKEN', 'REQUEST', 'COGNITO_USER_POOLS'
            ),
            volup.Optional('providerARNs'): [STRING_SCHEMA],
            volup.Optional('authType'): STRING_SCHEMA,
            volup.Optional('authorizerUri'): STRING_SCHEMA,
            volup.Optional('authorizerCredentials'): STRING_SCHEMA,
            volup.Optional('identitySource'): STRING_SCHEMA,
            volup.Optional('identityValidationExpression'): STRING_SCHEMA,
            volup.Optional('authorizerResultTtlInSeconds'): int,
        }],
        volup.Optional('integration'): {
            volup.Required('type'): STRING_SCHEMA,
            volup.Required('uri'): STRING_SCHEMA,
            volup.Optional('passthroughBehavior'): STRING_SCHEMA,
            volup.Optional('credentials'): STRING_SCHEMA,
        },
    })
    SERVICE_TYPE_S3 = 's3'
    SERVICE_TYPE_LAMBDA = 'lambda'
    SERVICE_TYPE_LAMBDA_APIGATEWAY = 'lambda-apigateway'
    SERVICE_TYPES = (
        SERVICE_TYPE_S3,
        SERVICE_TYPE_LAMBDA,
        SERVICE_TYPE_LAMBDA_APIGATEWAY,
    )
    SERVICES_SCHEMA = volup.Schema({
        # Many services, keyed by name
        STRING_SCHEMA: {  # service name, arbitary string
            volup.Required('type'): volup.Any(*SERVICE_TYPES),
            # TODO(larsbutler): Make these conditional on the service type (s3)
            volup.Optional('bucket_name'): STRING_SCHEMA,
            volup.Optional('build'): {
                volup.Required('working_dir'): STRING_SCHEMA,
                volup.Required('dist_dir'): STRING_SCHEMA,
                volup.Optional('include'): [STRING_SCHEMA],
                volup.Optional('dependencies'): STRING_SCHEMA,
            },
            volup.Optional('deploy'): {
                # TODO(larsbutler): Make these conditional on the service type
                # (lambda-apigateway)
                # Can be a simple dict, or a list of dicts as well.
                volup.Optional('apigateway'): APIGATEWAY_SCHEMA,
                # Only required for lambda/lambda-apigateway services.
                volup.Optional('lambda_function_configuration'): YOKE_LAMBDA_FN_CFG,
                volup.Optional('parameters'): PARAMETERS_SCHEMA,
            },
        }
    })
    # top-level schema
    YOLOFILE_SCHEMA = volup.Schema({
        volup.Required('name'): STRING_SCHEMA,
        volup.Required('accounts'): ACCOUNTS_SCHEMA,
        volup.Required('templates'): TEMPLATES_SCHEMA,
        volup.Required('stages'): STAGES_SCHEMA,
        volup.Required('services'): SERVICES_SCHEMA,
    })

    def __init__(self, content):
        """
        :param content:
            `dict` representation of the contents read from a yolo.yaml file.
        """
        self._raw_content = content
        self._validate()
        self.app_name = self._raw_content['name']

    @classmethod
    def from_file(cls, file_obj):
        """Load a yolo.yaml file from an open file-like object."""
        content = yaml.safe_load(file_obj)
        return cls(content)

    @classmethod
    def from_path(cls, path):
        """Load a yolo.yaml file given a path to the file."""
        with open(path) as fp:
            return cls.from_file(fp)

    def to_fileobj(self):
        """Dump this `YoloFile` contents to file-like object."""
        fp = utils.StringIO()
        yaml.dump(self._raw_content, fp, encoding='utf-8',
                  Dumper=yaml.RoundTripDumper)
        fp.seek(0)
        return fp

    def _validate(self):
        self.YOLOFILE_SCHEMA(self._raw_content)

    @property
    def accounts(self):
        return self._raw_content['accounts']

    @property
    def stages(self):
        return self._raw_content['stages']

    @property
    def templates(self):
        return self._raw_content['templates']

    @property
    def services(self):
        return self._raw_content['services']

    def get_stage_config(self, stage):
        if stage in self.stages:
            return self.stages[stage]
        else:
            # use the default/base stage config
            try:
                return self.stages[self.DEFAULT_STAGE]
            except KeyError:
                raise yolo.exceptions.YoloError(
                    'Unable to build custom stage config. Reason: No "default"'
                    'stage is defined.'
                )

    def normalize_account(self, account):
        """Take an account name or number and return an `AWSAccount` instance.

        This is meant to make commands more flexible so that the user can
        specify either the exact account number or the alias defined in the
        `accounts` section of the yolo.yml file.

        :param account:
            The account name or actual account number.
        :returns:
            :class:`AWSAccount` instance.
        :raises:
            :class:`yolo.exceptions.YoloError` if the account name or number
            can't be found.
        """
        # Check if it's an alias or a real number.
        account_name = None
        account_number = None
        default_region = None
        for acct in self.accounts:
            if acct['name'] == account:
                # We got an account alias
                account_name = account
                account_number = acct['account_number']
                default_region = acct['default_region']
                break
            elif acct['account_number'] == account:
                # We found a matching account
                account_name = acct['name']
                account_number = account
                default_region = acct['default_region']
                break
        else:
            # We didn't find a matching account number or alias
            raise yolo.exceptions.YoloError(
                'Unable to find a matching account number or alias for '
                '"{}"'.format(account)
            )
        return AWSAccount(
            name=account_name,
            account_number=account_number,
            default_region=default_region,
        )

    def render(self, **variables):
        # Render variables into the yolo file.
        template = jinja2.Template(
            yaml.dump(self._raw_content, Dumper=yaml.RoundTripDumper)
        )
        rendered_content = template.render(**variables)
        new_content = yaml.safe_load(rendered_content)
        return YoloFile(new_content)

    def _is_baseline_infrastructure_defined(self):
        return 'account' in self.templates
