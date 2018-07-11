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

from functools import wraps
import sys

import click

from yolo import client


# Click only supports --help by default.
CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])


def handle_yolo_errors(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except client.YoloError as exc:
            raise click.ClickException(str(exc))

    return wrapper


def deprecated(alt_command):
    """Show a deprecation warning and suggest an alternative command.

    :param str alt_command:
        Name of another ``yolo`` command to use instead of this one.
    """
    msg = 'DEPRECATED: Use `yolo {}` instead.'.format(alt_command)

    def deco(func):
        def inner(*args, **kwargs):
            """DEPRECATED."""
            # show warning with alt_command
            sys.stderr.write(msg + '\n')
            sys.stderr.flush()
            return func(*args, **kwargs)

        inner.__doc__ = msg
        return inner
    return deco


def stage_option(**attrs):
    """A --stage option for commands."""
    kwargs = dict(
        metavar='STAGE',
        required=False,
        help='Stage name.'
    )
    kwargs.update(attrs)
    option = click.option(
        '--stage',
        **kwargs
    )
    return option


def key_option(**attrs):
    """A --key option for commands."""
    kwargs = dict(
        metavar='KEY',
        required=False,
        help='Output key name.'
    )
    kwargs.update(attrs)
    option = click.option(
        '--key', multiple=True,
        **kwargs
    )
    return option


def format_option(**attrs):
    """A --format option for commands."""
    kwargs = dict(
        metavar='FORMAT',
        required=False,
        default='table',
        type=click.Choice(['table', 'json', 'value']),
        help='Output Format Type name.'
    )
    kwargs.update(attrs)
    option = click.option(
        '--format',
        **kwargs
    )
    return option


def service_option(**attrs):
    """A --service option for commands."""
    kwargs = dict(
        metavar='SERVICE',
        required=False,
        help='Service name.'
    )
    kwargs.update(attrs)
    option = click.option(
        '--service',
        **kwargs
    )
    return option


def account_option(**attrs):
    """A --account option for commands."""
    kwargs = dict(
        metavar='ACCOUNT',
        required=False,
        help='Account name or number.'
    )
    kwargs.update(attrs)
    option = click.option(
        '--account',
        **kwargs
    )
    return option


def yolo_file_option(**attrs):
    """A --yolo-file option for commands.

    Useful for specifying a specific yolo.yaml (instead of using the default).
    """
    kwargs = dict(
        metavar='YOLO_YAML',
        required=False,
        help='Choose an explicit config file.'
    )
    kwargs.update(attrs)
    option = click.option(
        '--yolo-file',
        '-f',
        **kwargs
    )
    return option


@click.group(
    context_settings=CONTEXT_SETTINGS,
)
def cli():
    """Manage infrastructure and services on AWS for multiple accounts/stages.

    (Or, "yolo everything into prod".)
    """


@cli.command(name='clear-config')
@handle_yolo_errors
def clear_config():
    """Clear cached configuration for `yolo`."""
    client.YoloClient().clear_config()


@cli.command(name='show-config')
@handle_yolo_errors
def show_config():
    """Show currently cached configuration.

    Don't show secrets."""
    client.YoloClient().show_config()


@cli.command()
@handle_yolo_errors
def login():
    """Login with and cache Rackspace credentials."""
    client.YoloClient().login()


@cli.command(name='use-profile')
@click.option('--profile-name', metavar='PROFILE_NAME')
@handle_yolo_errors
def use_profile(profile_name):
    """Make Yolo use an AWS CLI named profile."""
    client.YoloClient().use_profile(profile_name)


@cli.command(name='list-accounts')
@handle_yolo_errors
def list_accounts():
    """List AWS accounts."""
    client.YoloClient().list_accounts()


@cli.command(name='deploy-infra')
@account_option()
@stage_option()
@click.option(
    '--dry-run',
    '-n',
    is_flag=True,
    help='Show infrastructure change summary before actually doing it',
)
@yolo_file_option()
@click.option(
    '--asynchronous',
    '-a',
    is_flag=True,
    default=False,
    help=(
        "Run deployment operations as asynchronously as possible. "
        "(Don't wait for everything to finish before returning.)"
    ),
)
@click.option(
    '--recreate',
    '-r',
    is_flag=True,
    default=False,
    help=(
        'DANGER ZONE: Tear down and re-deploy infrastructure from scratch. '
        'Not allowed on account-level stacks.'
    ),
)
@handle_yolo_errors
def deploy_infra(yolo_file=None, **kwargs):
    """Deploy infrastructure from templates."""
    client.YoloClient(yolo_file=yolo_file).deploy_infra(**kwargs)


@cli.command()
@stage_option(required=False)
@yolo_file_option()
@handle_yolo_errors
def status(yolo_file=None, **kwargs):
    """Show infrastructure deployments status."""
    client.YoloClient(yolo_file=yolo_file).status(**kwargs)


@cli.command(name='build-lambda')
@service_option(required=True)
@stage_option(required=True)
@yolo_file_option()
@click.option(
    '--build-log',
    '-b',
    metavar='BUILD_LOG',
    type=click.File('a'),
    required=False,
    help='Target to file to save build logs',
)
@handle_yolo_errors
def build_lambda(yolo_file=None, **kwargs):
    """Build Lambda function packages."""
    client.YoloClient(yolo_file=yolo_file).build_lambda(**kwargs)


@cli.command(name='push')
@service_option(required=True)
@stage_option(required=True)
@yolo_file_option()
@handle_yolo_errors
def push(yolo_file=None, **kwargs):
    """Push a local build, ready it for deployment."""
    client.YoloClient(yolo_file=yolo_file).push(**kwargs)


@cli.command(name='list-builds')
@service_option(required=True)
@stage_option(required=True)
@yolo_file_option()
@handle_yolo_errors
def list_builds(yolo_file=None, **kwargs):
    """List the pushed builds for a service/stage."""
    client.YoloClient(yolo_file=yolo_file).list_builds(**kwargs)


@cli.command(name='deploy-lambda')
@service_option(required=True)
@stage_option(required=True)
@click.option(
    '--version',
    metavar='VERSION',
    help='Version of a build to deploy',
)
@click.option(
    '--from-local',
    is_flag=True,
    help='Deploy from a local ZIP file, instead of pushed artifacts.',
)
@click.option(
    '--timeout',
    metavar='SECONDS',
    type=int,
    help=(
        'Number of seconds the client will wait for a response from AWS. '
        'Might be useful to define a large enough value when network speed is '
        'slow.'
    ),
)
@yolo_file_option()
@handle_yolo_errors
def deploy_lambda(yolo_file=None, **kwargs):
    """Deploy Lambda functions for services."""
    client.YoloClient(yolo_file=yolo_file).deploy_lambda(**kwargs)


@cli.command(name='deploy-s3')
@service_option(required=True)
@stage_option(required=True)
@click.option(
    '--version',
    metavar='VERSION',
    required=True,
    help='Version of a build to deploy',
)
@yolo_file_option()
@handle_yolo_errors
def deploy_s3(yolo_file=None, **kwargs):
    """Deploy a built S3 application."""
    client.YoloClient(yolo_file=yolo_file).deploy_s3(**kwargs)


@cli.command()
@stage_option(required=True)
@yolo_file_option()
@handle_yolo_errors
def shell(yolo_file=None, **kwargs):
    """Launch a Python shell with AWS credentials."""
    client.YoloClient(yolo_file=yolo_file).shell(**kwargs)


@cli.command()
@account_option(required=False)
@stage_option(required=False)
@yolo_file_option()
@click.argument(
    'script',
    required=True,
)
@click.argument(
    'posargs',
    metavar='[ARGS]',
    required=False,
    nargs=-1,
)
@handle_yolo_errors
def run(yolo_file=None, **kwargs):
    """Run a script with AWS account credentials."""
    account = kwargs['account']
    stage = kwargs['stage']
    if (
        (account is None and stage is None) or
        (account is not None and stage is not None)
    ):
        raise click.UsageError(
            "One (and only one) of --account or --stage should be specified."
        )
    client.YoloClient(yolo_file=yolo_file).run(**kwargs)


@cli.command(name='show-parameters')
@service_option(required=True)
@stage_option(required=True)
@yolo_file_option()
@handle_yolo_errors
def show_parameters(yolo_file=None, **kwargs):
    """Show centralized config for a service/stage."""
    client.YoloClient(yolo_file=yolo_file).show_parameters(**kwargs)


@cli.command(name='put-parameters')
@service_option(required=True)
@stage_option(required=True)
@click.option(
    '--use-defaults/--no-use-defaults',
    default=False,
    help='Specify whether to use default values from the yolo.yml file.',
)
@click.option(
    '--param',
    metavar='PARAMETER',
    required=False,
    multiple=True,
    help=(
        'Set a specific named parameter. You can specify this flag multiple '
        'times.'
    ),
)
@click.option(
    '--copy-from-stage',
    '-c',
    metavar='STAGE',
    required=False,
    help=(
        'If possible, copy parameters from this stage. This can be used in '
        'conjunction with `--param` to copy explicit sets of parameters.'
    )
)
@yolo_file_option()
@handle_yolo_errors
def put_parameters(yolo_file=None, **kwargs):
    """Securely store service/stage parameters."""
    client.YoloClient(yolo_file=yolo_file).put_parameters(**kwargs)


@cli.command(name='ensure-parameters')
@service_option(required=True)
@stage_option(required=True)
@yolo_file_option()
@handle_yolo_errors
def ensure_parameters(yolo_file=None, **kwargs):
    """Ensure that all required parameters are defined in SSM."""
    client.YoloClient(yolo_file=yolo_file).ensure_parameters(**kwargs)


@cli.command(name='show-service')
@service_option(required=True)
@stage_option(required=True)
@yolo_file_option()
@handle_yolo_errors
def show_service(yolo_file=None, **kwargs):
    """Show service configuration for a given stage."""
    client.YoloClient(yolo_file=yolo_file).show_service(**kwargs)


@cli.command('show-outputs')
@account_option(required=False)
@stage_option(required=False)
@key_option(required=False)
@format_option(required=False)
@yolo_file_option()
@handle_yolo_errors
def show_outputs(yolo_file=None, **kwargs):
    """Show infrastructure stack outputs."""
    client.YoloClient(yolo_file=yolo_file).show_outputs(**kwargs)


@cli.command(name='push-lambda')
@service_option(required=True)
@stage_option(required=True)
@yolo_file_option()
@handle_yolo_errors
@deprecated(alt_command='push')
def push_lambda(yolo_file=None, **kwargs):
    """DEPRECATED: Use `yolo push`."""
    client.YoloClient(yolo_file=yolo_file).push(**kwargs)


@cli.command(name='upload-s3')
@service_option(required=True)
@stage_option(required=True)
@yolo_file_option()
@handle_yolo_errors
@deprecated(alt_command='push')
def upload_s3(yolo_file=None, **kwargs):
    """DEPRECATED: Use `yolo push`."""
    client.YoloClient(yolo_file=yolo_file).push(**kwargs)


@cli.command(name='list-s3-builds')
@service_option(required=True)
@stage_option(required=True)
@yolo_file_option()
@handle_yolo_errors
@deprecated(alt_command='list-builds')
def list_s3_builds(yolo_file=None, **kwargs):
    """DEPRECATED: Use `yolo list-builds`."""
    client.YoloClient(yolo_file=yolo_file).list_builds(**kwargs)


@cli.command(name='list-lambda-builds')
@service_option(required=True)
@stage_option(required=True)
@yolo_file_option()
@handle_yolo_errors
@deprecated(alt_command='list-builds')
def list_lambda_builds(yolo_file=None, **kwargs):
    """DEPRECATED: Use `yolo list-builds`."""
    client.YoloClient(yolo_file=yolo_file).list_builds(**kwargs)


@cli.command(name='deploy-baseline-infra')
@account_option(required=True)
@stage_option(required=True)
@click.option(
    '--dry-run',
    '-n',
    is_flag=True,
    help='Show infrastructure change summary before actually doing it',
)
@yolo_file_option()
@click.option(
    '--asynchronous',
    '-a',
    is_flag=True,
    default=False,
    help=(
        "Run deployment operations as asynchronously as possible. "
        "(Don't wait for everything to finish before returning.)"
    ),
)
@click.option(
    '--recreate',
    '-r',
    is_flag=True,
    default=False,
    help=(
        'DANGER ZONE: Tear down and re-deploy infrastructure from scratch. '
        'Not allowed on account-level stacks.'
    ),
)
@handle_yolo_errors
@deprecated(alt_command='deploy-infra')
def deploy_baseline_infra(yolo_file=None, **kwargs):
    """DEPRECATED: Use `yolo deploy-infra`."""
    client.YoloClient(yolo_file=yolo_file).deploy_infra(**kwargs)
