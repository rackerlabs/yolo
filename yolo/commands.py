import os

import yolo.const
import yolo.context
import yolo.yolo_file


def _get_yolo_file(yolo_file_path):
    if yolo_file_path is None:
        # If no yolo file was specified, look for it in the current
        # directory.
        path = None
        for filename in yolo.const.DEFAULT_FILENAMES:
            full_path = os.path.abspath(
                os.path.join(os.getcwd(), filename)
            )
            if os.path.isfile(full_path):
                path = full_path
                break
        else:
            raise Exception(
                'Yolo file could not be found, please specify one '
                'explicitly with --yolo-file or -f')
    else:
        path = os.path.abspath(yolo_file_path)

    # self._yolo_file_path = config_path
    return yolo.yolo_file.YoloFile.from_path(path)


def deploy_infra(yolo_file_path, stage=None, account=None, dry_run=False,
                 asynchronous=False, recreate=False):
    """Deploy infrastructure for an account or stage.

    :param str yolo_file_path:
        Path to a yolo config file.

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
    yolo_file = _get_yolo_file(yolo_file_path)

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

    # Figure out the credentials provider to use:
    if account is not None:
        pass
        # TODO:
    elif stage is not None:
        pass
        # Is there an explicit or implict account here?

        ###########
        # Implicit:
        ###########
        import yolo.credentials.aws
        stage_cfg = yolo_file.stages.get(
            # Get specific stage, if one is defined in the yolo config:
            stage,
            # Or, use the default one:
            yolo_file.stages.get('default')
        )
        account_cfg = yolo_file.accounts.get(stage_cfg.account)

        if account_cfg.credentials.provider == 'aws':
            creds_provider = yolo.credentials.aws.AWSCredentialsProvider(
                profile_name=account_cfg.credentials.profile
            )

        import pdb; pdb.set_trace()
        ###########
        # Explicit:
        ###########

    # Set up runtime context for rendering context variables in the yolo file
    # (i.e., filling in the blanks):
    # TODO: need the creds provider for this/might have to hit some AWS/FAWS
    # APIs
    # TODO: need to hit CloudFormation API to get `stage_outputs` and
    # `account_outputs`
    context = yolo.context.runtime_context(
        account_name=account,
        account_number=None,  # get from `creds_provider`
        account_outputs=None,

        stage_name=stage,
        stage_region=None,
        stage_outputs=None,
    )
