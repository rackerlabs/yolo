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

from __future__ import print_function

import code
import datetime
import getpass
import json
import logging
import subprocess
import os
import sys

import botocore.exceptions
import botocore.session
try:
    import bpython
    have_bpython = True
except ImportError:
    have_bpython = False
try:
    from IPython import start_ipython
    have_ipython = True
except ImportError:
    have_ipython = False
import keyring
import tabulate
try:
    from yoke.config import YokeConfig
    from yoke.shell import build as yoke_build
    from yoke.utils import decrypt as yoke_decrypt
    from yoke.utils import encrypt as yoke_encrypt
    have_yoke = True
except ImportError:
    have_yoke = False

from yolo.cloudformation import CloudFormation
from yolo import const
import yolo.exceptions
from yolo.exceptions import NoInfrastructureError
from yolo.exceptions import StackDoesNotExist
from yolo.exceptions import YoloError
from yolo import faws_client
from yolo.services import lambda_service
from yolo.services import s3_service
from yolo.utils import get_version_hash
from yolo import utils
from yolo.yolo_file import YoloFile


PY3 = sys.version_info >= (2, 8)
PY27 = not PY3
if PY27:
    input = raw_input  # noqa

logging.basicConfig(
    level=logging.WARNING,
    format=('%(asctime)s [%(levelname)s] '
            '[%(name)s.%(funcName)s:%(lineno)d]: %(message)s'),
    datefmt='%Y-%m-%d %H:%M:%S'
)
# Silence third-party lib loggers:
logging.getLogger('botocore').setLevel(logging.CRITICAL)
logging.getLogger('yoke').setLevel(logging.CRITICAL)
logging.getLogger('lambda_uploader').setLevel(logging.CRITICAL)

LOG = logging.getLogger(__name__)

SERVICE_TYPE_MAP = {
    YoloFile.SERVICE_TYPE_LAMBDA: lambda_service.LambdaService,
    YoloFile.SERVICE_TYPE_LAMBDA_APIGATEWAY: lambda_service.LambdaService,
    YoloFile.SERVICE_TYPE_S3: s3_service.S3Service,
}


class FakeYokeArgs(object):

    def __init__(self, func, config):
        self.func = func
        self.config = config


class YoloClient(object):

    def __init__(self, yolo_file=None):
        self._yolo_file_path = yolo_file
        self._yolo_file = None
        self._faws_client = None

        # Credentials for accessing FAWS accounts:
        self._rax_username = None
        self._rax_api_key = None

        self._version_hash = None

        # This will get populated when the ``yolo_file`` is read and the basic
        # account/stage information (including stack outputs) is read.
        self._context = None

    @property
    def rax_username(self):
        if self._rax_username is None:
            self._rax_username = (
                os.getenv(const.RACKSPACE_USERNAME) or
                keyring.get_password(const.NAMESPACE, 'rackspace_username')
            )
            if self._rax_username is None:
                # Couldn't find credentials in keyring or environment:
                raise YoloError(
                    'Missing credentials: Run `yolo login` or set the '
                    'environment variable "{}"'.format(const.RACKSPACE_USERNAME)
                )
        return self._rax_username

    @property
    def rax_api_key(self):
        if self._rax_api_key is None:
            self._rax_api_key = (
                os.getenv(const.RACKSPACE_API_KEY) or
                keyring.get_password(const.NAMESPACE, 'rackspace_api_key')
            )
            if self._rax_api_key is None:
                # Couldn't find credentials in keyring or environment:
                raise YoloError(
                    'Missing credentials: Run `yolo login` or set the '
                    'environment variable "{}"'.format(const.RACKSPACE_API_KEY)
                )
        return self._rax_api_key

    @property
    def context(self):
        """Environment context for commands and template rendering."""
        if self._context is None:
            raise RuntimeError('Environment context is not yet loaded!')
        else:
            return self._context

    @property
    def yolo_file(self):
        if self._yolo_file is None:
            self._yolo_file = self._get_yolo_file(self._yolo_file_path)
        return self._yolo_file

    @property
    def faws_client(self):
        """Lazily instantiate a FAWS client."""
        if self._faws_client is None:
            self._faws_client = faws_client.FAWSClient(
                self.rax_username, self.rax_api_key
            )

        return self._faws_client

    @property
    def version_hash(self):
        if self._version_hash is None:
            self._version_hash = get_version_hash()
        return self._version_hash

    @property
    def now_timestamp(self):
        """Get the current UTC time as a timestamp string.

        Example: '2017-05-11_19-44-47-110436'
        """
        return datetime.datetime.utcnow().strftime('%Y-%m-%d_%H-%M-%S-%f')

    @property
    def app_bucket_name(self):
        return '{}-{}'.format(
            self.yolo_file.app_name, self.context.account.account_number
        )

    @property
    def account_stack_name(self):
        return self.get_account_stack_name(self.context.account.account_number)

    def get_account_stack_name(self, account_number):
        return '{}-BASELINE-{}'.format(self.yolo_file.app_name, account_number)

    @property
    def account_bucket_name(self):
        # NOTE(larsbutler): The account bucket and account stack have slightly
        # different names for good reasons:
        # - The stack name retains the uppercase BASELINE for backwards
        #   compatibility with existing stacks.
        # - The bucket name has been changed to lowercase 'baseline' in order
        #   to work correctly with S3 in regions outside of us-east-1. See
        #   http://docs.aws.amazon.com/AmazonS3/latest/dev/BucketRestrictions.html
        return '{}-baseline-{}'.format(
            self.yolo_file.app_name,
            self.context.account.account_number,
        )

    def _get_service_client(self, service):
        service_cfg = self.yolo_file.services.get(service)
        if service_cfg is None:
            raise YoloError(
                'Unknown service "{service}". Valid services: '
                '{services}.'.format(
                    service=service,
                    services=', '.join(sorted(self.yolo_file.services.keys())),
                )
            )
        service_client = SERVICE_TYPE_MAP[service_cfg['type']](
            self.yolo_file, self.faws_client, self.context
            # TODO: add timeout
        )
        return service_client

    def get_stage_outputs(self, account_number, region, stage):
        cf_client = self.faws_client.aws_client(account_number, 'cloudformation', region)
        cf = CloudFormation(cf_client)
        stack_name = self.get_stage_stack_name(account_number, stage)
        try:
            return cf.get_stack_outputs(stack_name=stack_name)
        except StackDoesNotExist:
            raise YoloError(
                'Stage infrastructure stack does not exist; please run '
                '"yolo deploy-infra --stage {}" first.'.format(stage)
            )

    def get_account_outputs(self, account_number, region):
        cf_client = self.faws_client.aws_client(account_number, 'cloudformation', region)
        cf = CloudFormation(cf_client)
        stack_name = self.get_account_stack_name(account_number)
        # Full account-level data might not be available, when the baseline
        # stack doesn't exist. We should only allow this to happen, when there's
        # no baseline infrastructure defined.
        try:
            return cf.get_stack_outputs(stack_name=stack_name)
        except StackDoesNotExist:
            LOG.info(
                'Account-level stack does not exist yet for account %s,',
                account_number
            )
            return {}

    def _get_metadata(self):
        return {
            'timestamp': datetime.datetime.utcnow().isoformat(),
            'version_hash': get_version_hash(),
        }

    def set_up_yolofile_context(self, stage=None, account=None):
        """Set up yolofile context to render template variables.

        :param str stage:
            Name of stage on which to base the built context object.
            Use this if stage information is available. If ``stage`` is
            supplied, it is not necessary to supply ``account`` as well
            because the account info can be inferred from the stage config.
        :param str account:
            Name of account on which to base the built context object.
            Use this when account information is available but stage
            information is not.
        """
        context = utils.DottedDict(
            metadata=self._get_metadata(),
            stage={'outputs': {}, 'region': None, 'name': None},
            account={'outputs': {}, 'account_number': None, 'name': None},
        )
        if stage is not None:
            stage_cfg = self.yolo_file.get_stage_config(stage)
            account_cfg = self.yolo_file.normalize_account(
                stage_cfg['account']
            )

            # Account templates are optional:
            if 'account' in self.yolo_file.templates:
                # get account stack outputs
                account_stack_outputs = self.get_account_outputs(
                    account_cfg.account_number,
                    self.yolo_file.templates['account']['region']
                )
            else:
                account_stack_outputs = {}

            account_context = utils.DottedDict(
                name=account_cfg.name,
                account_number=account_cfg.account_number,
                outputs=account_stack_outputs,
            )

            # get stage stack outputs
            try:
                stage_stack_outputs = self.get_stage_outputs(
                    account_cfg.account_number, stage_cfg['region'], stage
                )
            except YoloError:
                # The stack for this stage doesn't exist (at least, not yet).
                stage_stack_outputs = {}
            stage_context = utils.DottedDict(
                name=stage,
                region=stage_cfg['region'],
                outputs=stage_stack_outputs,
            )

            context['stage'] = stage_context
            context['account'] = account_context
        else:
            if account is not None:
                account_cfg = self.yolo_file.normalize_account(account)

                # get account stack outputs
                account_stack_outputs = self.get_account_outputs(
                    account_cfg.account_number,
                    self.yolo_file.templates['account']['region']
                )
                account_context = utils.DottedDict(
                    name=account_cfg.name,
                    account_number=account_cfg.account_number,
                    outputs=account_stack_outputs,
                )
                context['account'] = account_context

        self._context = context

    def get_stage_stack_name(self, account_number, stage):
        return '{}-{}-{}'.format(
            self.yolo_file.app_name,
            account_number,
            stage,
        )

    def get_aws_account_credentials(self, account_number):
        creds = self.faws_client.get_aws_account_credentials(account_number)
        cred = creds['credential']
        cred_vars = dict(
            AWS_ACCESS_KEY_ID=cred['accessKeyId'],
            AWS_SECRET_ACCESS_KEY=cred['secretAccessKey'],
            AWS_SESSION_TOKEN=cred['sessionToken'],
        )
        return cred_vars

    def _setup_aws_credentials_in_environment(self, acct_num, region):
        os.environ['AWS_DEFAULT_REGION'] = region
        aws_session = self.faws_client.boto3_session(acct_num)
        credentials = aws_session.get_credentials()
        os.environ['AWS_ACCESS_KEY_ID'] = credentials.access_key
        os.environ['AWS_SECRET_ACCESS_KEY'] = credentials.secret_key
        if credentials.token:
            os.environ['AWS_SESSION_TOKEN'] = credentials.token

    def _get_yolo_file(self, yolo_file):
        if yolo_file is None:
            # If no yolo file was specified, look for it in the current
            # directory.
            config_path = None
            for filename in const.DEFAULT_FILENAMES:
                full_path = os.path.abspath(
                    os.path.join(os.getcwd(), filename)
                )
                if os.path.isfile(full_path):
                    config_path = full_path
                    break
            else:
                raise Exception(
                    'Yolo file could not be found, please specify one '
                    'explicitly with --yolo-file or -f')
        else:
            config_path = os.path.abspath(yolo_file)

        self._yolo_file_path = config_path
        yf = YoloFile.from_path(self._yolo_file_path)
        return yf

    def _stages_accounts_regions(self, yf, stage):
        # If stage specific, show only status for that stage
        if stage is not None:
            if stage == YoloFile.DEFAULT_STAGE:
                raise YoloError('Invalid stage "{}"'.format(stage))
            elif stage != YoloFile.DEFAULT_STAGE and stage in yf.stages:
                stgs_accts_regions = set([
                    (stage,
                     yf.stages[stage]['account'],
                     yf.stages[stage]['region'])
                ])
            else:
                # stage is not in the config file; it must be an ad-hoc stage
                # use the account number and region from the 'default' stage
                stgs_accts_regions = set([
                    (stage,
                     yf.stages[YoloFile.DEFAULT_STAGE]['account'],
                     yf.stages[YoloFile.DEFAULT_STAGE]['region'])
                ])
        # No stage specified; show status for all stages
        else:
            stgs_accts_regions = set([
                (stg_name, stg['account'], stg['region'])
                for stg_name, stg in yf.stages.items()
            ])
        return stgs_accts_regions

    def _ensure_bucket(self, acct_num, region, bucket_name):
        s3_client = self.faws_client.boto3_session(acct_num).client(
            's3', region_name=region
        )
        try:
            print('checking for bucket {}...'.format(bucket_name))
            s3_client.head_bucket(Bucket=bucket_name)
        except botocore.exceptions.ClientError as err:
            print('bucket "{}" does not exist.  creating...'.format(
                bucket_name)
            )
            if str(err) == const.BUCKET_NOT_FOUND:
                create_bucket_kwargs = {
                    'ACL': 'private',
                    'Bucket': bucket_name,
                }
                if not region == 'us-east-1':
                    # You can only specify a location constraint for regions
                    # which are not us-east-1. For us-east-1, you just don't
                    # specify anything--which is kind of silly.
                    create_bucket_kwargs['CreateBucketConfiguration'] = {
                        'LocationConstraint': region
                    }
                s3_client.create_bucket(**create_bucket_kwargs)
        s3 = self.faws_client.boto3_session(acct_num).resource('s3', region_name=region)
        bucket = s3.Bucket(bucket_name)
        return bucket

    def _create_or_update_stack(self, cf_client, stack_name, master_url,
                                stack_params, tags, recreate=False,
                                dry_run=False, asynchronous=False,
                                force=False):
        if dry_run:
            # Dry run only makes sense for updates, not creates.
            self._update_stack_dry_run(
                cf_client, stack_name, master_url, stack_params, tags,
            )
        else:
            self._do_create_or_update_stack(
                cf_client, stack_name, master_url, stack_params, tags,
                recreate=recreate, asynchronous=asynchronous, force=force,
            )

    def _update_stack_dry_run(self, cf_client, stack_name,
                              master_url, stack_params, tags):
        """Perform a dry run stack update and output the proposed changes.

        :param str stack_name:
            The name of the CloudFormation stack on which to perform a dry run.
        :param str master_url:
            S3 URL where the "master" CloudFormation stack template is located.
        """
        cf = CloudFormation(cf_client)
        stack_exists, stack_details = cf.stack_exists(stack_name)
        if not stack_exists:
            raise YoloError(
                'Unable to perform dry run: No stack exists yet.'
            )

        LOG.warning('Calculating --dry-run details...')

        result = cf.create_change_set(
            stack_name, master_url, stack_params, tags
        )
        change_set_id = result['Id']

        # Get the full details of the change set:
        change_set_desc = cf_client.describe_change_set(
            ChangeSetName=change_set_id,
            StackName=stack_name,
        )

        # Get the current stack details:
        [stack_desc] = cf_client.describe_stacks(
            StackName=stack_name
        )['Stacks']

        output = utils.StringIO()

        # Show the changes:
        output.write('Resource Changes:\n')
        json.dump(
            change_set_desc['Changes'], output, indent=2, sort_keys=True
        )

        # Show a diff of the parameters:
        output.write('\n\nParameter Changes:\n')
        param_diff = self._get_param_diff(stack_desc, change_set_desc)
        output.write(param_diff)

        # Show a diff of the tags:
        output.write('\n\nTags Changes:\n')
        tag_diff = self._get_tag_diff(stack_desc, change_set_desc)
        output.write(tag_diff)

        # Show a diff of the full template:
        output.write('\n\nTemplate Changes:\n')
        template_diff = self._get_template_diff(
            cf_client,
            dict(StackName=stack_name),
            dict(StackName=stack_name, ChangeSetName=change_set_id),
            fromfile=stack_name,
            tofile='{}-dry-run'.format(stack_name),
        )
        output.write(template_diff)

        output.seek(0)
        print(output.read())

        # Clean up after ourselves; we don't want to leave a bunch of stale
        # changes sets lying around.
        cf_client.delete_change_set(
            StackName=stack_name, ChangeSetName=change_set_id
        )

    def _get_param_diff(self, stack_a_desc, stack_b_desc):
        """Calculate the diff of params from two CloudFormation stacks.

        The parameters passed in here can either be a stack description or a
        change set description.

        :returns:
            A unified diff of the parameters as a multiline string.
            Parameters will be converted to a simple dictionary of key/value
            pairs, in place of the verbose list structure favored by
            CloudFormation.
        """
        # Convert params into simple dicts
        a_params = {
            x['ParameterKey']: x['ParameterValue']
            for x in stack_a_desc['Parameters']
        }
        b_params = {
            x['ParameterKey']: x['ParameterValue']
            for x in stack_b_desc['Parameters']
        }
        # Get fake file names to feed into the diff (to make it more readable):
        fromfile = stack_a_desc.get('StackName')
        tofile = stack_b_desc.get('StackName')
        return utils.get_unified_diff(
            a_params, b_params, fromfile=fromfile, tofile=tofile
        )

    def _get_tag_diff(self, stack_a_desc, stack_b_desc):
        """Diff the tags from two CloudFormation stack descriptions.

        :returns:
            A unified diff of the tags as a multiline string. Tags will be
            converted to a simple dictionary of key/value pairs, in place of
            the verbose list structure favored by CloudFormation.
        """
        a_tags = {
            x['Key']: x['Value']
            for x in stack_a_desc['Tags']
        }
        b_tags = {
            x['Key']: x['Value']
            for x in stack_b_desc['Tags']
        }
        # Get fake file names to feed into the diff (to make it more readable):
        fromfile = stack_a_desc.get('StackName')
        tofile = stack_b_desc.get('StackName')
        return utils.get_unified_diff(
            a_tags, b_tags, fromfile=fromfile, tofile=tofile
        )

    def _get_template_diff(self, cf_client, a_stack, b_stack, fromfile=None,
                           tofile=None):
        """Diff templates used for two different stacks/change sets.

        :param cf_client:
            boto3 CloudFormation client.
        :param dict a_stack:
            Dict containing at least a StackName key (and optionally
            ChangeSetName).
        :param dict b_stack:
            Dict containing at least a StackName key (and optionally
            ChangeSetName).
        :param str fromfile:
            Optional "file name" to include in the diff to represent the "from"
            version.
        :param str tofile:
            Optional "file name" to include in the diff to represent the "to"
            version.

        :returns:
            A unified diff of the templates as a multiline string. "File names"
            included in the diff represent the names of each respective
            stack/change set.

            Note that if the two templates are drastically different (such a
            difference of yaml vs. json), the diff won't be very useful.
        """
        a_template = cf_client.get_template(**a_stack)['TemplateBody']
        b_template = cf_client.get_template(**b_stack)['TemplateBody']
        return utils.get_unified_diff(
            a_template, b_template, fromfile=fromfile, tofile=tofile,
        )

    def _do_create_or_update_stack(self, cf_client, stack_name, master_url,
                                   stack_params, tags, recreate=False,
                                   asynchronous=False,
                                   force=False):
        cf = CloudFormation(cf_client)
        stack_exists, stack_details = cf.stack_exists(stack_name)

        # TODO(larsbutler): Show stack status after an operation has completed.
        try:
            if not stack_exists:
                cf.create_stack(
                    stack_name, master_url, stack_params, tags,
                    asynchronous=asynchronous
                )
            elif stack_exists and recreate:
                # This assignment asserts that there is only one stack in the
                # list. This should always be the case. If not, something has
                # gone wrong.
                [the_stack] = stack_details['Stacks']
                if const.YOLO_STACK_TAGS['protected'] in the_stack['Tags']:
                    # The stack is protected.
                    if not force:
                        # We can't touch this stack.
                        raise YoloError(
                            'Unable to re-create stack: Stack is protected and'
                            ' probably for a good reason. Use --force (with '
                            'caution) to override.'
                        )

                cf.recreate_stack(
                    stack_name, master_url, stack_params, tags,
                    stack_details, asynchronous=asynchronous
                )
            elif stack_exists and not recreate:
                cf.update_stack(
                    stack_name, master_url, stack_params,
                    asynchronous=asynchronous
                )
        except botocore.exceptions.ClientError as err:
            if 'No updates are to be performed' in str(err):
                # Nothing changed
                print('No changes to apply to stack.')
            elif 'ValidationError' in str(err):
                # TODO(szilveszter): We can actually figure out the
                # actual issue, skipping that for now.
                # Examples:
                # botocore.exceptions.ClientError: An error occurred (ValidationError) when calling the CreateStack operation: TemplateURL must reference a valid S3 object to which you have access.  # noqa
                # botocore.exceptions.ClientError: An error occurred (ValidationError) when calling the CreateStack operation: Template format error: YAML not well-formed. (line 10, column 26)  # noqa
                print('Something is wrong with the CloudFormation template.')
                raise YoloError(err)
            else:
                raise YoloError(err)

    def show_config(self):
        print('Rackspace user: {}'.format(self.rax_username))

    def clear_config(self):
        keyring.delete_password(const.NAMESPACE, 'rackspace_username')
        keyring.delete_password(const.NAMESPACE, 'rackspace_api_key')

    def login(self):
        # Get RACKSPACE_USERNAME and RACKSPACE_API_KEY envvars
        # prompt for them interactively.
        # The envvar approach works scripted commands, while the interactive
        # mode is preferred for executing on the command line (by a human).
        self._rax_username = get_username()
        self._rax_api_key = get_api_key(self.rax_username)

        # TODO(larsbutler): perform login against the rackspace identity api

        # store them in keyring:
        keyring.set_password(
            const.NAMESPACE, 'rackspace_username', self.rax_username
        )
        keyring.set_password(
            const.NAMESPACE, 'rackspace_api_key', self.rax_api_key
        )
        print('login successful!')

    def list_accounts(self):
        accounts = self.faws_client.list_aws_accounts()
        headers = ['Account Number', 'Name', 'Service Level']
        aws_accounts = accounts['awsAccounts']
        table = [headers]
        for aws_account in aws_accounts:
            table.append([
                aws_account['awsAccountNumber'],
                aws_account['name'],
                const.ACCT_SVC_LVL_MAPPING[aws_account['serviceLevelId']],
            ])
        print(tabulate.tabulate(table, headers='firstrow'))

    def deploy_infra(self, stage=None, account=None, dry_run=False,
                     asynchronous=False, recreate=False, force=False):
        """Deploy infrastructure for an account or stage."""
        with_stage = stage is not None
        with_account = account is not None

        # You must specify stage or account, but not both.
        if not ((with_stage and not with_account) or
                (not with_stage and with_account)):
            raise YoloError('You must specify either --stage or --account (but'
                            ' not both).')
        if account is not None:
            if recreate:
                raise YoloError(
                    'Recreating account-level stacks is not allowed (for '
                    'safety purposes). You will need to tear down the stack '
                    'manually.'
                )
            if 'account' not in self.yolo_file.templates:
                raise YoloError('No "account" templates are defined.')

        self.set_up_yolofile_context(stage=stage, account=account)
        self._yolo_file = self.yolo_file.render(**self.context)

        if stage is not None:
            # Deploy stage-level templates
            region = self.context.stage.region
            bucket_folder_prefix = (
                const.BUCKET_FOLDER_PREFIXES['stage-templates'].format(
                    stage=self.context.stage.name, timestamp=self.now_timestamp
                )
            )
            templates_cfg = self.yolo_file.templates['stage']
            tags = [const.YOLO_STACK_TAGS['created-with-yolo-version']]
            # TODO(larsbutler): Add `protected` attribute to the
            # ``self.context.stage`` so that we don't have to fetch stage
            # config to get it.
            stage_cfg = self.yolo_file.get_stage_config(stage)
            if stage_cfg.get('protected', False):
                tags.append(const.YOLO_STACK_TAGS['protected'])
            stack_name = self.get_stage_stack_name(
                self.context.account.account_number,
                self.context.stage.name,
            )
        else:
            # Deploy account-level templates:
            region = self.yolo_file.templates['account']['region']
            bucket_folder_prefix = (
                const.BUCKET_FOLDER_PREFIXES['account-templates'].format(
                    timestamp=self.now_timestamp
                )
            )
            templates_cfg = self.yolo_file.templates['account']
            tags = [
                const.YOLO_STACK_TAGS['created-with-yolo-version'],
                # Always protect account-level infra stacks:
                const.YOLO_STACK_TAGS['protected'],
            ]
            stack_name = self.account_stack_name

        bucket = self._ensure_bucket(
            self.context.account.account_number,
            region,
            self.app_bucket_name,
        )

        self._deploy_stack(
            stack_name,
            templates_cfg,
            bucket_folder_prefix,
            bucket,
            self.context.account.account_number,
            region,
            tags,
            dry_run=dry_run,
            asynchronous=asynchronous,
            recreate=recreate,
            force=force,
        )

    def _deploy_stack(self, stack_name, templates_cfg, bucket_folder_prefix,
                      bucket, acct_num, region, tags,
                      dry_run=False, asynchronous=False, recreate=False,
                      force=False):
        if os.path.isabs(templates_cfg['path']):
            full_templates_dir = templates_cfg['path']
        else:
            # Template dir is relative to the location of the yolo.yaml file.
            working_dir = os.path.dirname(self._yolo_file_path)
            full_templates_dir = os.path.join(
                working_dir, templates_cfg['path']
            )

        files = os.listdir(full_templates_dir)
        # filter out yaml/json files
        cf_files = [
            f for f in files
            if (f.endswith('yaml') or
                f.endswith('yml') or
                f.endswith('json'))
        ]
        [master_template_file] = [
            f for f in cf_files
            if f.startswith('master.')
        ]
        # If there were no template files found, let's stop here with a friendly
        # error message.
        if len(cf_files) == 0:
            print('No CloudFormation template files found.')
            return

        for cf_file in cf_files:
            cf_file_full_path = os.path.join(full_templates_dir, cf_file)
            bucket_key = '{}/{}'.format(bucket_folder_prefix, cf_file)
            print('uploading s3://{}/{}...'.format(bucket.name, bucket_key))
            bucket.upload_file(
                Filename=cf_file_full_path,
                Key=bucket_key,
                ExtraArgs=const.S3_UPLOAD_EXTRA_ARGS,
            )

        cf_client = self.faws_client.boto3_session(acct_num).client(
            'cloudformation',
            region_name=region,
        )
        # TODO(larsbutler): detect json, yaml, or yml for the master.* file.
        # Defaults to master.yaml for now.
        # TODO(larsbutler): Check for master.* template file and show a nice
        # error message if it is not present.
        master = '{}/{}'.format(bucket_folder_prefix, master_template_file)
        # This is the URL to the bucket.
        master_url = 'https://s3.amazonaws.com/{}/{}'.format(
            bucket.name, master
        )
        stack_params = [
            dict(ParameterKey=k, ParameterValue=v)
            for k, v in templates_cfg['params'].items()
        ]

        try:
            self._create_or_update_stack(
                cf_client, stack_name, master_url, stack_params, tags,
                dry_run=dry_run, recreate=recreate, asynchronous=asynchronous,
                force=force,
            )
        except yolo.exceptions.CloudFormationError as err:
            # Re-raise it as a friendly error message:
            raise YoloError(str(err))

    def deploy_baseline_infra(self, account, dry_run=False,
                              asynchronous=False):
        """Deploy baseline infrastructure for a given account."""
        self.set_up_yolofile_context(account=account)
        self._yolo_file = self.yolo_file.render(**self.context)

        bucket = self._ensure_bucket(
            self.context.account.account_number,
            self.yolo_file.templates['account']['region'],
            self.app_bucket_name
        )

        bucket_folder_prefix = (
            const.BUCKET_FOLDER_PREFIXES['account-templates'].format(
                timestamp=self.now_timestamp
            )
        )

        tags = [
            const.YOLO_STACK_TAGS['created-with-yolo-version'],
            # Always protect baseline infra stacks:
            const.YOLO_STACK_TAGS['protected'],
        ]

        self._deploy_stack(
            self.account_stack_name,
            self.yolo_file.templates['account'],
            bucket_folder_prefix,
            bucket,
            self.context.account.account_number,
            self.yolo_file.templates['account']['region'],
            tags,
            dry_run=dry_run,
            asynchronous=asynchronous,
        )

    def status(self, stage=None):
        self.set_up_yolofile_context()
        self._yolo_file = self.yolo_file.render(**self.context)

        # else, show status for all stages
        headers = ['StackName', 'Description', 'StackStatus']
        table = [headers]

        # TODO(larsbutler): Validate `stage`
        stgs_accts_regions = self._stages_accounts_regions(self.yolo_file, stage)
        stack_names = set()

        for stg_name, account, region in stgs_accts_regions:
            aws_account = self.yolo_file.normalize_account(account)
            cf_client = self.faws_client.aws_client(
                aws_account.account_number, 'cloudformation', region_name=region
            )
            if stg_name == YoloFile.DEFAULT_STAGE:
                stacks_paginator = cf_client.get_paginator('list_stacks')
                for page in stacks_paginator.paginate():
                    for stack in page['StackSummaries']:
                        if (
                            stack['StackName'].startswith(self.yolo_file.app_name) and
                            stack['StackStatus'] != 'DELETE_COMPLETE'
                        ):
                            if stack['StackName'] not in stack_names:
                                table.append([
                                    stack['StackName'],
                                    stack.get('TemplateDescription', ''),
                                    stack['StackStatus'],
                                ])
                                stack_names.add(stack['StackName'])
            else:
                # It's an explicit stage name so we can statically query on the
                # stack.
                stack_name = '{}-{}-{}'.format(
                    self.yolo_file.app_name,
                    aws_account.account_number,
                    stg_name,
                )
                try:
                    stack_desc = cf_client.describe_stacks(StackName=stack_name)
                except botocore.exceptions.ClientError as err:
                    if 'does not exist' in str(err):
                        # Doesn't exist; nothing to show.
                        pass
                else:
                    stack = stack_desc['Stacks'][0]
                    if stack['StackName'] not in stack_names:
                        table.append([
                            stack['StackName'],
                            stack.get('Description', ''),
                            stack['StackStatus'],
                        ])
                        stack_names.add(stack['StackName'])

        # Only print table if we have at least one stack to display.
        if len(table) > 1:
            print(tabulate.tabulate(table, headers='firstrow'))
        else:
            if stage is None:
                raise NoInfrastructureError(
                    'No infrastructure found for any stage. Run "yolo '
                    'deploy-infra" first.'
                )
            else:
                raise NoInfrastructureError(
                    'No infrastructure found for stage "{}". Run "yolo '
                    'deploy-infra" first.'.format(stage)
                )

    def build_lambda(self, stage, service):
        if not have_yoke:
            raise YoloError('ERROR: yoke is required for build to work.')

        self.set_up_yolofile_context(stage=stage)
        self._yolo_file = self.yolo_file.render(**self.context)

        # Set up AWS credentials for yoke
        self._setup_aws_credentials_in_environment(
            self.context.account.account_number,
            self.context.stage.region,
        )

        lambda_svc = lambda_service.LambdaService(
            self.yolo_file, self.faws_client, self.context
        )
        lambda_svc.build(service, stage)

    def push(self, service, stage):
        # TODO(larsbutler): Make the "version" a parameter, so the user
        # can explicitly specify it on the command line. Could be useful
        # for releases and the like.
        self.set_up_yolofile_context(stage=stage)
        self._yolo_file = self.yolo_file.render(**self.context)

        service_client = self._get_service_client(service)

        bucket = self._ensure_bucket(
            self.context.account.account_number,
            self.context.stage.region,
            self.app_bucket_name,
        )
        service_client.push(service, stage, bucket)

    def list_builds(self, service, stage):
        self.set_up_yolofile_context(stage=stage)
        self._yolo_file = self.yolo_file.render(**self.context)

        service_client = self._get_service_client(service)

        bucket = self._ensure_bucket(
            self.context.account.account_number,
            self.context.stage.region,
            self.app_bucket_name
        )
        service_client.list_builds(service, stage, bucket)

    def deploy_lambda(self, service, stage, version, from_local, timeout):
        if not have_yoke:
            raise YoloError('ERROR: yoke is required for deploy to work.')

        if version is None and not from_local:
            raise YoloError(
                'ERROR: You have to either specify a version, or use '
                '--from-local.'
            )
        if version is not None and from_local:
            raise YoloError(
                'ERROR: You can only specify one of --version or --from-local,'
                ' but not both.'
            )

        self.set_up_yolofile_context(stage=stage)
        self._yolo_file = self.yolo_file.render(**self.context)

        # TODO(larsbutler): Check if service is actually
        # lambda/lambda-apigateway. If it isn't, throw an error.

        # Set up AWS credentials for yoke
        self._setup_aws_credentials_in_environment(
            self.context.account.account_number,
            self.context.stage.region,
        )

        bucket = self._ensure_bucket(
            self.context.account.account_number,
            self.context.stage.region,
            self.app_bucket_name,
        )

        if timeout is None:
            timeout = lambda_service.LambdaService.DEFAULT_TIMEOUT
        lambda_svc = lambda_service.LambdaService(
            self.yolo_file, self.faws_client, self.context, timeout
        )
        if from_local:
            lambda_svc.deploy_local_version(service, stage)
        else:
            lambda_svc.deploy(service, stage, version, bucket)

    def encrypt_yoke_secrets(self, stage, service):
        if not have_yoke:
            raise YoloError(
                'yoke is required for encrypt-yoke-secrets to work.')

        self.set_up_yolofile_context(stage=stage)
        self._yolo_file = self.yolo_file.render(**self.context)
        if service not in self.yolo_file.services.keys():
            raise YoloError(
                'Service could not be found in the Yolo file: {}'.format(
                    service,
                ))
        stage_cfg = self.yolo_file.get_stage_config(stage)
        # Set up AWS credentials for yoke
        self._setup_aws_credentials_in_environment(
            self.context.account.account_number, stage_cfg['region'])

        service_config = self.yolo_file.services[service]
        if service_config['type'] not in (
            YoloFile.SERVICE_TYPE_LAMBDA,
            YoloFile.SERVICE_TYPE_LAMBDA_APIGATEWAY,
        ):
            # Nothing to do for now if it isn't a Lambda-type service.
            raise YoloError(
                "Service type '{}' not supported by Yoke.".format(
                    service_config['type'],
                ))

        # Fake it 'till we make it: prepare data to be passed over to yoke.
        args = FakeYokeArgs(func=yoke_build, config=None)
        yoke_config = service_config['yoke']
        # Get the working directory of the service yoke is handling.
        # Default to the current directory.
        yoke_working_dir = os.path.abspath(
            yoke_config.get('working_dir', '.'))
        env_dict = yoke_config.get('environment', {})
        yoke_stage = yoke_config.get('stage', stage)
        config = YokeConfig(
            shellargs=args,
            project_dir=yoke_working_dir,
            stage=yoke_stage,
            env_dict=env_dict)
        yoke_encrypt(config.get_config(skip_decrypt=True), output=True)

    def decrypt_yoke_secrets(self, stage, service):
        if not have_yoke:
            raise YoloError(
                'yoke is required for encrypt-yoke-secrets to work.')

        self.set_up_yolofile_context(stage=stage)
        self._yolo_file = self.yolo_file.render(**self.context)
        if service not in self.yolo_file.services.keys():
            raise YoloError(
                'Service could not be found in the Yolo file: {}'.format(
                    service,
                ))
        stage_cfg = self.yolo_file.get_stage_config(stage)
        # Set up AWS credentials for yoke
        self._setup_aws_credentials_in_environment(
            self.context.account.account_number, stage_cfg['region'])

        service_config = self.yolo_file.services[service]
        if service_config['type'] not in (
            YoloFile.SERVICE_TYPE_LAMBDA,
            YoloFile.SERVICE_TYPE_LAMBDA_APIGATEWAY,
        ):
            # Nothing to do for now if it isn't a Lambda-type service.
            raise YoloError(
                "Service type '{}' not supported by Yoke.".format(
                    service_config['type'],
                ))

        # Fake it 'till we make it: prepare data to be passed over to yoke.
        args = FakeYokeArgs(func=yoke_build, config=None)
        yoke_config = service_config['yoke']
        # Get the working directory of the service yoke is handling.
        # Default to the current directory.
        yoke_working_dir = os.path.abspath(
            yoke_config.get('working_dir', '.'))
        env_dict = yoke_config.get('environment', {})
        yoke_stage = yoke_config.get('stage', stage)
        config = YokeConfig(
            shellargs=args,
            project_dir=yoke_working_dir,
            stage=yoke_stage,
            env_dict=env_dict)
        yoke_decrypt(config.get_config(skip_decrypt=True), output=True)

    def deploy_s3(self, stage, service, version):
        self.set_up_yolofile_context(stage=stage)
        self._yolo_file = self.yolo_file.render(**self.context)

        # Builds bucket:
        bucket = self._ensure_bucket(
            self.context.account.account_number,
            self.context.stage.region,
            self.app_bucket_name,
        )

        s3_svc = s3_service.S3Service(
            self.yolo_file, self.faws_client, self.context
        )
        s3_svc.deploy(service, stage, version, bucket)

    def shell(self, stage):
        self.set_up_yolofile_context(stage=stage)
        self._yolo_file = self.yolo_file.render(**self.context)

        # Set up AWS credentials for the shell
        self._setup_aws_credentials_in_environment(
            self.context.account.account_number,
            self.context.stage.region,
        )

        # Select Python shell
        if have_bpython:
            bpython.embed()
        elif have_ipython:
            start_ipython(argv=[])
        else:
            code.interact()

    def run(self, account, stage, script, posargs=None):
        if posargs is None:
            posargs = []

        region = None
        if account is not None:
            self.set_up_yolofile_context(account=account)
        elif stage is not None:
            self.set_up_yolofile_context(stage=stage)
            region = self.context.stage.region

        cred_vars = self.get_aws_account_credentials(
            self.context.account.account_number
        )
        if region is not None:
            cred_vars['AWS_DEFAULT_REGION'] = region

        # TODO(larsbutler): Make it optional for the user to carefully tailor
        # the environment settings for the executed script.
        sp_env = os.environ.copy()
        sp_env.update(cred_vars)
        sp_args = [script]
        sp_args.extend(posargs)
        sp = subprocess.Popen(sp_args, env=sp_env)
        # TODO(larsbutler): Get stdout and stderr
        sp.wait()

    def show_parameters(self, service, stage):
        self.set_up_yolofile_context(stage=stage)
        self._yolo_file = self.yolo_file.render(**self.context)

        # get ssm client
        ssm_client = self.faws_client.aws_client(
            self.context.account.account_number,
            'ssm',
            self.context.stage.region,
        )

        param_path = '/{service}/{stage}/latest/'.format(
            service=service, stage=stage
        )

        results = ssm_client.get_parameters_by_path(
            Path=param_path, WithDecryption=True
        )
        headers = ['Name', 'Value']
        table = [headers]

        # NOTE(larsbutler, 5-Sep-2017): Multiline config items (like certs,
        # private keys, etc.) won't get displayed properly unless you use the
        # latest trunk version of python-tabulate. It does still have some
        # issues with exact spacing of outputs, but at least it works to
        # display things properly.
        for param in results['Parameters']:
            table.append((param['Name'].split(param_path)[1], param['Value']))
        print(tabulate.tabulate(table, headers='firstrow'))
        # NOTE(larsbutler, 6-Sep-2017): If a parameter is removed from the
        # yolofile, it will still be in SSM. Probably the best/safest way to
        # handle the cleanup is for someone to manually remove it. Yolo could
        # help here by showing a warning when we encounter parameters in SSM
        # that aren't in the yolofile.

    def put_parameters(self, service, stage, param=None, use_defaults=False):
        if param is None:
            param = tuple()

        self.set_up_yolofile_context(stage=stage)
        self._yolo_file = self.yolo_file.render(**self.context)

        service_cfg = self.yolo_file.services[service]
        # Get stage specific parameter config, or get the default if this is
        # an ad-hoc/custom stage.
        parameters = service_cfg['parameters']['stages'].get(
            # TODO(larsbutler): handle index errors if no default defined.
            stage, service_cfg['parameters']['stages']['default']
        )

        if len(param) > 0:
            # Only set specific params.
            # We need to raise an error if one of the user specified params
            # doesn't exist for service/stage.
            unknown_params = sorted(list(set(param).difference(
                set(x['name'] for x in parameters)
            )))
            if unknown_params:
                # The user specified a param that isn't defined in the
                # yolofile.
                raise YoloError(
                    'Unknown parameter(s): {}'.format(
                        ', '.join(unknown_params)
                    )
                )

            # Filter down the parameters to only what the user specified:
            parameters = [x for x in parameters if x['name'] in param]

        # get ssm client
        ssm_client = self.faws_client.aws_client(
            self.context.account.account_number,
            'ssm',
            self.context.stage.region,
        )

        for param_item in parameters:
            param_name = param_item['name']
            param_value = None

            # If explicit param names were listed by the user and use_defaults
            # is set:
            if param is not None and use_defaults:
                # Look for a default value from the yolo.yml. There might not
                # be one.
                param_value = param_item.get('value')

            if param_value is None:
                if param_item.get('multiline', False):
                    # Multiline entry:
                    print(
                        'Enter "{}" multiline value '
                        '(ctrl+d when finished):'.format(param_name),
                        end=''
                    )
                    param_value = sys.stdin.read()
                else:
                    # Single line entry:
                    param_value = getpass.getpass(
                        'Enter "{}" value: '.format(param_name)
                    )
            else:
                print(
                    'Setting "{}" to "{}"'.format(param_name, param_value)
                )

            param_name = '/{service}/{stage}/latest/{key}'.format(
                service=service,
                stage=stage,
                key=param_name,
            )
            ssm_client.put_parameter(
                Name=param_name,
                Value=param_value,
                # Always encrypt everything, just for good measure:
                Type='SecureString',
                # TODO: allow extension in the yolo file to use a custom KMS
                # key. It could be an output from an account/stage CF stack.
                Overwrite=True,
            )
        print('Environment configuration complete!')

    def show_service(self, service, stage):
        self.set_up_yolofile_context(stage=stage)
        self._yolo_file = self.yolo_file.render(**self.context)

        lambda_svc = lambda_service.LambdaService(
            self.yolo_file, self.faws_client, self.context
        )
        lambda_svc.show(service, stage)

    def show_outputs(self, stage=None, account=None):
        with_stage = stage is not None
        with_account = account is not None

        # You must specify stage or account, but not both.
        if not ((with_stage and not with_account) or
                (not with_stage and with_account)):
            raise YoloError('You must specify either --stage or --account (but'
                            ' not both).')

        self.set_up_yolofile_context(stage=stage, account=account)

        if with_stage:
            outputs = self.get_stage_outputs(
                self.context.account.account_number,
                self.context.stage.region,
                stage,
            )
        elif with_account:
            outputs = self.get_account_outputs(
                self.context.account.account_number,
                self.context.stage.region,
            )
        table = [('Name', 'Value')]
        for output in sorted(outputs.items()):
            table.append(output)
        print(tabulate.tabulate(table, headers='firstrow'))


def get_username():
    username = input('Rackspace username: ')
    return username


def get_api_key(username):
    api_key = getpass.getpass(prompt='API key for {}: '.format(username))
    return api_key
