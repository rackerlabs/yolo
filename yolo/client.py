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
import tempfile

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

from yolo.cloudformation import CloudFormation
from yolo import const
from yolo.credentials.aws_cli import AWSCLICredentials
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

        # AWS CLI named profile
        self._aws_profile_name = None

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
    def aws_profile_name(self):
        if self._aws_profile_name is None:
            self._aws_profile_name = (
                os.getenv(const.AWS_PROFILE_NAME) or
                keyring.get_password(const.NAMESPACE, 'aws_profile_name')
            )

        # We can allow this value to be None, because in that case we'll
        # fallback to FAWS credentials.
        return self._aws_profile_name

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
            # NOTE(szilveszter): This is just a quick hack, because I wanted
            # to avoid refactoring everything. If this ends up being a good
            # approach, I'm happy to do the work.
            # If we have a profile stored, let's use it instead of going to
            # FAWS first.
            if self.aws_profile_name:
                self._faws_client = AWSCLICredentials(self.aws_profile_name)
            else:
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

    def _get_service_cfg(self, service):
        service_cfg = self.yolo_file.services.get(service)
        if service_cfg is None:
            raise YoloError(
                'Unknown service "{service}". Valid services: '
                '{services}.'.format(
                    service=service,
                    services=', '.join(sorted(self.yolo_file.services.keys())),
                )
            )
        return service_cfg

    def _get_service_client(self, service):
        service_cfg = self._get_service_cfg(service)
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
                    account_cfg.default_region,
                )
            else:
                account_stack_outputs = {}

            account_context = utils.DottedDict(
                name=account_cfg.name,
                account_number=account_cfg.account_number,
                outputs=account_stack_outputs,
                default_region=account_cfg.default_region,
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
                    account_cfg.default_region,
                )
                account_context = utils.DottedDict(
                    name=account_cfg.name,
                    account_number=account_cfg.account_number,
                    outputs=account_stack_outputs,
                    default_region=account_cfg.default_region,
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
        """Make sure an S3 bucket exists in the specified account/region.

        If it doesn't exist, create it.

        :param str acct_num:
            AWS account number.
        :param str region:
            AWS region in which to create the bucket (e.g., us-east-1,
            eu-west-1, etc.).
        :param str bucket_name:
            Name of the target bucket.

        :returns:
            :class:`boto3.resources.factory.s3.Bucket` instance.
        """
        s3_client = self.faws_client.aws_client(
            acct_num, 's3', region_name=region
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
                                stack_params, tags, asynchronous=False,
                                dry_run=False, protected=False,
                                recreate=False):
        """Create a new or update an existing stack.

        :param cf_client:
            :class:`botocore.client.CloudFormation` instance.
        :param str stack_name:
            Unique name of the stack to create or update.
        :param str master_url:
            URL location (in S3) of the "master" CloudFormation template to use
            for creating/updating a stack.
        :param list stack_params:
            (Optional.) A list of parameters to pass to the CloudFormation API
            call. Each list item is a dict which must contain the keys
            ``ParameterKey`` and ``ParameterValue``.

            Alternatively, you can specify ``UsePreviousValue`` instead of
            ``ParameterValue``. This only applies to stack updates, not
            creation.
        :param list tags:
            A list of tags apply to the CloudFormation stack. Each
            list item is a dict containing the keys ``Key`` and ``Value``.
        :param bool asynchronous:
            Stack creates/updates may take a while to complete, sometimes more
            than 40 minutes depending on the change. Set this to ``true`` to
            return as soon as possible and let CloudFormation handle the
            change. By default ``asynchronous`` is set to ``false``, which
            means that we block and wait for the stack create/update to finish
            before returning.
        :param bool dry_run:
            Set to ``true`` to perform a dry run and show the proposed changes
            without actually applying them.
        :param bool protected:
            If ``true``, make sure that stack termination protection is enabled
            (whether it is a new or existing stack). Note that setting this to
            ``false`` will not disable protection; that must be done manually.
        :param bool recreate:
            This only applies to stack updates.

            If ``true``, tear down and re-create the stack from scratch.
            Otherwise, just try to update the existing stack.
        """
        if dry_run:
            # Dry run only makes sense for updates, not creates.
            self._update_stack_dry_run(
                cf_client, stack_name, master_url, stack_params, tags,
            )
        else:
            self._do_create_or_update_stack(
                cf_client, stack_name, master_url, stack_params, tags,
                recreate=recreate, asynchronous=asynchronous,
                protected=protected,
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

        # Show the changes:
        print('Resource Changes:')
        print(
            json.dumps(
                change_set_desc['Changes'], indent=2, sort_keys=True
            )
        )

        # Show a diff of the parameters:
        print('\nParameter Changes:')
        param_diff = self._get_param_diff(stack_desc, change_set_desc)
        print(param_diff)

        # Show a diff of the tags:
        print('\nTags Changes:')
        tag_diff = self._get_tag_diff(stack_desc, change_set_desc)
        print(tag_diff)

        # Show a diff of the full template:
        print('\nTemplate Changes:')
        template_diff = self._get_template_diff(
            cf_client,
            dict(StackName=stack_name),
            dict(StackName=stack_name, ChangeSetName=change_set_id),
            fromfile=stack_name,
            tofile='{}-dry-run'.format(stack_name),
        )
        print(template_diff)

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
                                   protected=False):
        """Actually perform the stack create/update.

        For parameter info, see :meth:`_create_or_update_stack`.
        """
        cf = CloudFormation(cf_client)
        stack_exists, stack_details = cf.stack_exists(stack_name)

        # TODO(larsbutler): Show stack status after an operation has completed.
        try:
            if not stack_exists:
                cf.create_stack(
                    stack_name, master_url, stack_params, tags,
                    asynchronous=asynchronous, protected=protected,
                )
            elif stack_exists and recreate:
                # This assignment asserts that there is only one stack in the
                # list. This should always be the case. If not, something has
                # gone wrong.

                # Before recreating the stack, we need to check if it's
                # protected. There are two ways to protect a stack:
                # 1. Add a `yolo:Protected` tag to the stack. Yolo will set
                # this tag automatically for `protected` stacks to "true"
                # (technically it just needs to be set to any value, according
                # to the logic below).
                # 2. Use the new CF termination protection feature:
                # http://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/using-cfn-protect-stacks.html.
                #
                # The second approach is preferred, so if a stack is protected
                # with the first (older) approach, apply termination protection
                # just as an added layer of security.
                [the_stack] = stack_details['Stacks']
                is_protected = False
                if the_stack.get('EnableTerminationProtection', False):
                    LOG.info('Stack "%s" is protected by CloudFormation.',
                             stack_name)
                    # The stack is protected using the new approach (CF
                    # termination protection).
                    is_protected = True
                elif const.YOLO_STACK_TAGS['protected'] in the_stack['Tags']:
                    LOG.info('Stack "%s" is protected by Yolo.', stack_name)
                    is_protected = True
                    # Since we bothered to look, enable hard termination
                    # protection while we're here:
                    LOG.info('Adding CloudFormation termination protection to '
                             'stack "%s"...', stack_name)
                    cf_client.update_termination_protection(
                        EnableTerminationProtection=True,
                        StackName=stack_name,
                    )

                if is_protected:
                    # We can't touch this stack.
                    raise YoloError(
                        'Unable to re-create stack "{}": Stack is protected '
                        'and probably for a good reason.'.format(stack_name)
                    )
                else:
                    # Go ahead and recreate it.
                    cf.recreate_stack(
                        stack_name, master_url, stack_params, tags,
                        stack_details, asynchronous=asynchronous,
                        protected=is_protected,
                    )
            elif stack_exists and not recreate:
                cf.update_stack(
                    stack_name, master_url, stack_params,
                    asynchronous=asynchronous, protected=protected,
                )
        except botocore.exceptions.ClientError as err:
            if 'No updates are to be performed' in str(err):
                # Nothing changed
                print('No changes to apply to stack.')
            elif 'TerminationProtection is enabled' in str(err):
                raise YoloError(
                    'Stack "{}" is protected; deletion is not allowed.'.format(
                        stack_name
                    )
                )
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
        except yolo.exceptions.CloudFormationError:
            possible_cause = 'unknown'
            stack_events = cf_client.describe_stack_events(
                StackName=stack_name
            ).get('StackEvents', [])
            for stack_event in stack_events:
                if 'ResourceStatusReason' in stack_event:
                    # Find the first error and report it.
                    possible_cause = stack_event['ResourceStatusReason']
                    # It may not be the root cause.
                    break

            raise YoloError(
                'Infrastructure template failed to deploy. '
                'Possible cause: "{}"\nCheck the CloudFormation dashboard '
                'for more details.'.format(possible_cause)
            )

    def show_config(self):
        # NOTE(larsbutler): Generally we access the `rax_username` using the
        # property, but in this case it is acceptable to have an
        # empty/non-configured username in order to show the state of the
        # application config.
        print('Rackspace user: {}'.format(self._rax_username or ''))
        print('AWS CLI named profile: {}'.format(self.aws_profile_name))

    def clear_config(self):
        keyring.delete_password(const.NAMESPACE, 'rackspace_username')
        keyring.delete_password(const.NAMESPACE, 'rackspace_api_key')
        keyring.delete_password(const.NAMESPACE, 'aws_profile_name')

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

    def use_profile(self, profile_name):
        if profile_name is None:
            # NOTE(szilveszter): At some point we could read the profiles from
            # the credentials files, and we could ask the user to choose one.
            raise YoloError(
                "Please specify a profile with the '--profile-name' option."
            )

        self._aws_profile_name = profile_name
        keyring.set_password(
            const.NAMESPACE, 'aws_profile_name', self.aws_profile_name
        )

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
                     asynchronous=False, recreate=False):
        """Deploy infrastructure for an account or stage.

        :param str stage:
            name of the stage for which to create/update infrastructure.

            You can specify either ``stage`` or ``account``, but not both.
        :param str account:
            Name or number of the account for which to create/update
            infrastructure.

            You can specify either ``stage`` or ``account``, but not both.
        :param bool asynchronous:
            Stack creates/updates may take a while to complete, sometimes more
            than 40 minutes depending on the change. Set this to ``true`` to
            return as soon as possible and let CloudFormation handle the
            change. By default ``asynchronous`` is set to ``false``, which
            means that we block and wait for the stack create/update to finish
            before returning.
        :param bool dry_run:
            Set to ``true`` to perform a dry run and show the proposed changes
            without actually applying them.
        :param bool recreate:
            This only applies to stack updates.

            If ``true``, tear down and re-create the stack from scratch.
            Otherwise, just try to update the existing stack.
        """
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
            self._deploy_stage_stack(
                dry_run=dry_run,
                asynchronous=asynchronous,
                recreate=recreate,
            )
        else:
            # Deploy account-level templates:
            self._deploy_account_stack(
                dry_run=dry_run,
                asynchronous=asynchronous,
            )

    def _deploy_stage_stack(self, dry_run=False, asynchronous=False,
                            recreate=False):
        """Deploy stage-level infrastructure for the current context.

        :param bool dry_run:
            Set to ``true`` to perform a dry run and show the proposed changes
            without actually applying them.
        :param bool asynchronous:
            Stack creates/updates may take a while to complete, sometimes more
            than 40 minutes depending on the change. Set this to ``true`` to
            return as soon as possible and let CloudFormation handle the
            change. By default ``asynchronous`` is set to ``false``, which
            means that we block and wait for the stack create/update to finish
            before returning.
        :param bool recreate:
            This only applies to stack updates.

            If ``true``, tear down and re-create the stack from scratch.
            Otherwise, just try to update the existing stack.
        """
        region = self.context.stage.region
        bucket_folder_prefix = (
            const.BUCKET_FOLDER_PREFIXES['stage-templates'].format(
                stage=self.context.stage.name, timestamp=self.now_timestamp
            )
        )
        templates_cfg = self.yolo_file.templates['stage']

        stack_name = self.get_stage_stack_name(
            self.context.account.account_number,
            self.context.stage.name,
        )

        # TODO(larsbutler): Add `protected` attribute to the
        # ``self.context.stage`` so that we don't have to fetch stage
        # config to get it.
        protected = False
        stage_cfg = self.yolo_file.get_stage_config(self.context.stage.name)
        if stage_cfg.get('protected', False):
            protected = True

        self._deploy_stack(
            stack_name,
            templates_cfg['path'],
            templates_cfg['params'],
            bucket_folder_prefix,
            region,
            dry_run=dry_run,
            asynchronous=asynchronous,
            recreate=recreate,
            protected=protected,
        )

    def _deploy_account_stack(self, dry_run=False,
                              asynchronous=False):
        """Deploy account-level infrastructure for the current context.

        :param bool dry_run:
            Set to ``true`` to perform a dry run and show the proposed changes
            without actually applying them.
        :param bool asynchronous:
            Stack creates/updates may take a while to complete, sometimes more
            than 40 minutes depending on the change. Set this to ``true`` to
            return as soon as possible and let CloudFormation handle the
            change. By default ``asynchronous`` is set to ``false``, which
            means that we block and wait for the stack create/update to finish
            before returning.
        """
        region = self.context.account.default_region
        bucket_folder_prefix = (
            const.BUCKET_FOLDER_PREFIXES['account-templates'].format(
                timestamp=self.now_timestamp
            )
        )
        templates_cfg = self.yolo_file.templates['account']
        stack_name = self.account_stack_name

        self._deploy_stack(
            stack_name,
            templates_cfg['path'],
            templates_cfg['params'],
            bucket_folder_prefix,
            region,
            dry_run=dry_run,
            asynchronous=asynchronous,
            # Always protect account-level infra stacks:
            protected=True,
        )

    def _deploy_stack(self, stack_name, templates_path, templates_params,
                      bucket_folder_prefix, region, asynchronous=False,
                      dry_run=False, protected=False, recreate=False):
        """Deploy the specified template to a new or existing stack.

        :param str stack_name:
            Unique name of the stack to create or update.
        :param str templates_path:
            File system directory location from which to get CloudFormation
            templates for this deployment.
        :param dict templates_params:
            Dict of key/value pairs to input as parameters to the
            CloudFormation stack deployment.
        :param str bucket_folder_prefix:
            Location in the yolo S3 bucket to store CloudFormation templates.
            Template files will be copied from the local file system to this
            location.
        :param str region:
            AWS region in which to create the bucket (e.g., us-east-1,
            eu-west-1, etc.).
        :param bool asynchronous:
            Stack creates/updates may take a while to complete, sometimes more
            than 40 minutes depending on the change. Set this to ``true`` to
            return as soon as possible and let CloudFormation handle the
            change. By default ``asynchronous`` is set to ``false``, which
            means that we block and wait for the stack create/update to finish
            before returning.
        :param bool dry_run:
            Set to ``true`` to perform a dry run and show the proposed changes
            without actually applying them.
        :param bool protected:
            If ``true``, make sure that stack termination protection is enabled
            (whether it is a new or existing stack). Note that setting this to
            ``false`` will not disable protection; that must be done manually.
        :param bool recreate:
            This only applies to stack updates.

            If ``true``, tear down and re-create the stack from scratch.
            Otherwise, just try to update the existing stack.
        """
        tags = [const.YOLO_STACK_TAGS['created-with-yolo-version']]
        if protected:
            tags.append(const.YOLO_STACK_TAGS['protected'])

        bucket = self._ensure_bucket(
            self.context.account.account_number,
            region,
            self.app_bucket_name,
        )

        if os.path.isabs(templates_path):
            full_templates_dir = templates_path
        else:
            # Template dir is relative to the location of the yolo.yaml file.
            working_dir = os.path.dirname(self._yolo_file_path)
            full_templates_dir = os.path.join(
                working_dir, templates_path
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

        cf_client = self.faws_client.aws_client(
            self.context.account.account_number,
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
            for k, v in templates_params.items()
        ]

        try:
            self._create_or_update_stack(
                cf_client, stack_name, master_url, stack_params, tags,
                dry_run=dry_run, recreate=recreate, asynchronous=asynchronous,
                protected=protected,
            )
        except yolo.exceptions.CloudFormationError as err:
            # Re-raise it as a friendly error message:
            raise YoloError(str(err))

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

    def build_lambda(self, stage, service, build_log=None):
        if build_log is None:
            _, build_log_path = tempfile.mkstemp()
            build_log = open(build_log_path, 'a')

        try:
            self.set_up_yolofile_context(stage=stage)
            self._yolo_file = self.yolo_file.render(**self.context)

            lambda_svc = lambda_service.LambdaService(
                self.yolo_file, self.faws_client, self.context
            )
            lambda_svc.build(service, stage, build_log)
        finally:
            build_log.close()

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
        exit_status = sp.wait()
        if not exit_status == 0:
            raise YoloError(
                'Command exited with non-zero ({}) status'.format(exit_status)
            )

    def show_parameters(self, service, stage):
        params = self._get_ssm_parameters(service, stage)

        # NOTE(larsbutler, 5-Sep-2017): Multiline config items (like certs,
        # private keys, etc.) won't get displayed properly unless you use the
        # latest trunk version of python-tabulate. It does still have some
        # issues with exact spacing of outputs, but at least it works to
        # display things properly.
        headers = ['Name', 'Value']
        table = [headers]
        for param_name in sorted(params.keys()):
            # Show params in the table in alphabetical order.
            table.append((param_name, params[param_name]))

        print(tabulate.tabulate(table, headers='firstrow'))
        # NOTE(larsbutler, 6-Sep-2017): If a parameter is removed from the
        # yolofile, it will still be in SSM. Probably the best/safest way to
        # handle the cleanup is for someone to manually remove it. Yolo could
        # help here by showing a warning when we encounter parameters in SSM
        # that aren't in the yolofile.

    def _get_ssm_parameters(self, service, stage, param_names=None):
        """Fetch stored parameters in SSM for a given service/stage.

        :returns:
            `dict` of param name/param value key/value pairs.
        """
        self._get_service_cfg(service)
        self.set_up_yolofile_context(stage=stage)
        self._yolo_file = self.yolo_file.render(**self.context)

        params = {}

        ssm_client = self.faws_client.aws_client(
            self.context.account.account_number,
            'ssm',
            self.context.stage.region,
        )
        param_path = '/{service}/{stage}/latest/'.format(
            service=service, stage=stage
        )

        def _save_params_from_response(response):
            for param in response['Parameters']:
                param_name = param['Name'].split(param_path)[1]
                params[param_name] = param['Value']

        response = ssm_client.get_parameters_by_path(
            Path=param_path, WithDecryption=True
        )
        _save_params_from_response(response)
        next_token = response.get('NextToken')
        while next_token is not None:
            response = ssm_client.get_parameters_by_path(
                Path=param_path, WithDecryption=True, NextToken=next_token
            )
            _save_params_from_response(response)
            next_token = response.get('NextToken')

        return params

    def put_parameters(self, service, stage, param=None, use_defaults=False,
                       copy_from_stage=None):
        copied_params = {}
        if copy_from_stage is not None:
            # Try to copy parameters from another stage.
            copied_params = self._get_ssm_parameters(service, copy_from_stage)

        if param is None:
            param = tuple()

        self.set_up_yolofile_context(stage=stage)
        self._yolo_file = self.yolo_file.render(**self.context)

        service_cfg = self._get_service_cfg(service)
        # Get the default parameters first, if available.
        parameters = service_cfg['deploy']['parameters']['stages'].get(
            'default', []
        )
        # Convert the list to a dict, so that it can be easily overridden by
        # stage-specific parameters.
        parameters_dict = {p['name']: p for p in parameters}
        # Get the stage-specific parameters.
        stage_parameters = service_cfg['deploy']['parameters']['stages'].get(
            stage, []
        )
        stage_parameters_dict = {p['name']: p for p in stage_parameters}
        # Override default parameters with any stage-specific ones.
        parameters_dict.update(stage_parameters_dict)
        # Convert back to a list that we'll use going forward.
        parameters = parameters_dict.values()

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

        # Precedence for setting params:
        #   - use default (if available), possibly calculating it from context
        #     variables
        #   - copy from target stage (if applicable)
        #   - prompt for value
        for param_item in parameters:
            param_name = param_item['name']
            param_value = None

            # If --use-defaults is set:
            if use_defaults:
                # Look for a default value from the yolo.yml. There might not
                # be one.
                param_value = param_item.get('value')

            # If param has no value yet and --copy-from-stage option was
            # specified:
            if param_value is None and copy_from_stage is not None:
                if param_name in copied_params:
                    # Maybe we can get the value from the copied params.
                    param_value = copied_params[param_name]

            # We couldn't get a value for the param from either another stage
            # or from default/calculated values. We need to prompt the user for
            # the parameter value:
            if param_value is None:
                # If it's a multiline param, use an appropriate multiline
                # prompt.
                if param_item.get('multiline', False):
                    # Multiline entry:
                    print(
                        'Enter "{}" multiline value '
                        '(ctrl+d when finished):'.format(param_name),
                        end=''
                    )
                    sys.stdout.flush()
                    param_value = sys.stdin.read()
                else:
                    # Otherwise, just get a single line entry using non-echoing
                    # input.
                    param_value = getpass.getpass(
                        'Enter "{}" value: '.format(param_name)
                    )

            print('Setting parameter "{}"...'.format(param_name))

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

    def ensure_parameters(self, service, stage):
        # Get the current for a given service/stage from SSM:
        ssm_params = self._get_ssm_parameters(service, stage)
        ssm_param_names = set(ssm_params.keys())

        # Get the parameter names that are defined in the yolo file:
        service_cfg = self.yolo_file.services[service]
        parameters_cfg = service_cfg['deploy']['parameters']['stages'].get(
            stage,
            service_cfg['deploy']['parameters']['stages']['default']
        )
        yolo_param_names = set([param['name'] for param in parameters_cfg])

        missing_params = yolo_param_names.difference(ssm_param_names)
        if missing_params:
            raise yolo.exceptions.YoloError(
                'The following parameters were not found in SSM '
                'for "--service {service}" and "--stage {stage}":'
                '\n\t- {missing_params}'
                '\nTo fix this, try running '
                '`yolo put-parameters ' '--service {service} '
                '--stage {stage}`.'.format(
                    missing_params='\n\t- '.join(sorted(missing_params)),
                    service=service,
                    stage=stage,
                )
            )
        else:
            print('All required parameters are stored in SSM')

    def show_service(self, service, stage):
        self.set_up_yolofile_context(stage=stage)
        self._yolo_file = self.yolo_file.render(**self.context)

        lambda_svc = lambda_service.LambdaService(
            self.yolo_file, self.faws_client, self.context
        )
        lambda_svc.show(service, stage)

    def show_outputs(self, stage=None, account=None, key=None, format=None):
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
                self.context.account.default_region,
            )

        if len(key) > 0:
            # Drop everything except for the specified keys.
            outputs = {k: outputs[k] for k in key}
        if format == 'json':
            print(json.dumps(outputs, sort_keys=True, indent=2))
        elif format == 'table':
            table = [('Name', 'Value')]
            for output in sorted(outputs.items()):
                table.append(output)
            print(tabulate.tabulate(table, headers='firstrow'))
        elif format == 'value':
            for output in outputs.values():
                print(output)


def get_username():
    username = input('Rackspace username: ')
    return username


def get_api_key(username):
    api_key = getpass.getpass(prompt='API key for {}: '.format(username))
    return api_key
