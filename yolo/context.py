import datetime
import os
import subprocess

from dotted_dict import DottedDict


def runtime_context(
        timestamp=None, version_hash=None, account_name=None,
        account_number=None, account_region=None, account_outputs=None,
        stage_name=None, stage_region=None, stage_outputs=None):
    """
    :param str timestamp:
        ISO standard UTC timestamp. Example: "2018-04-20T10:33:18.797374".
    :param str version_hash:
        Codebase current version identifier.

    :param str account_name:
        Name of the current account as it is defined in the yolo.yaml.
    :param str account_number:
        Account number of the current AWS account.
    :param str account_region:
        Default region for this account.
    :param dict account_outputs:
        Key/value pairs of output-name/output-value from account stacks.

    :param str stage_name:
        Name of the current stage, if any.
    :param str stage_region:
        Region for the current stage, if any.
    :param dict stage_outputs:
        Key/value pairs of output-name/output-value from stage stacks.

    TODO: What about the service name?

    :returns:
        `DottedDict`
    """
    # timestamp is the only attribute that is absolutelyrequired.
    if timestamp is None:
        timestamp = datetime.datetime.utcnow().isoformat()
    if version_hash is None:
        # FIXME: this could be `None`. Empty
        version_hash = get_version_hash()

    ctx = DottedDict({
        'timestamp': timestamp,
    })

    # Only set context variables that are actually present;
    # this avoids accidentally using None/null values when a required context
    # variable is referenced.
    if version_hash is not None:
        ctx.version_hash = version_hash

    ctx.account = DottedDict()
    ctx.account.outputs = {}
    if account_name is not None:
        ctx.account.name = account_name
    if account_number is not None:
        ctx.account.account_number = account_number
    if account_outputs is not None:
        ctx.account.outputs = account_outputs
    if account_region is not None:
        ctx.account.default_region = account_region

    ctx.stage = DottedDict()
    ctx.stage.outputs = {}
    if stage_name is not None:
        ctx.stage.name = stage_name
    if stage_region is not None:
        ctx.stage.region = stage_region
    if stage_outputs is not None:
        ctx.stage.outputs = stage_outputs

    return ctx


def get_version_hash():
    # Let's look for the easy way: CircleCI environment variable
    # FIXME(larsbutler): This is a bit prescriptive. We should remove this.
    sha1 = os.environ.get('CIRCLE_SHA1', None)
    if sha1 is not None:
        return sha1

    try:
        sha1 = subprocess.check_output(
            'git log -1 | head -1 | cut -d" " -f2',
            shell=True,
        ).decode('utf-8').strip()
    except Exception as exc:
        print('Could not determine SHA1: {}'.format(exc))
        sha1 = None

    return sha1
