import logging
import os
import tempfile

import botocore.exceptions
import tabulate
from yoke.config import YokeConfig
from yoke.shell import build as yoke_build

import yolo.client
from yolo import const
import yolo.exceptions
import yolo.services
from yolo import utils
from yolo import yolo_file

LOG = logging.getLogger(__name__)


class LambdaService(yolo.services.BaseService):
    """Collection of functions for managing Lambda-based services.

    This includes basic Lambda services as well as Lambda services wired up to
    API Gateway.
    """

    def __init__(self, *args, **kwargs):
        super(LambdaService, self).__init__(*args, **kwargs)
        # YoloFile object which is loaded when dealing with a remote build
        # (which contains a snapshot of a yolo.yaml file).
        self.build_yolo_file = None

    def build(self, service, stage):
        """Create build artifacts for a Lambda-based service.

        :param str service:
            Name of the service to build. See ``services`` in the yolo.yml
            file.
        :param str stage:
            Name of the for which the build has been created.
        """
        print('Building {service} for stage "{stage}"'.format(
            service=service, stage=stage
        ))
        service_cfg = self.yolo_file.services[service]

        if service_cfg['type'] not in (
            yolo_file.YoloFile.SERVICE_TYPE_LAMBDA,
            yolo_file.YoloFile.SERVICE_TYPE_LAMBDA_APIGATEWAY,
        ):
            # Nothing to do if it's not a Lambda-type service.
            raise yolo.exceptions.YoloError(
                'Service "{}" is not a Lambda service.'.format(service)
            )

        # Fake it 'till we make it: prepare data to be passed over to yoke.
        args = yolo.client.FakeYokeArgs(func=yoke_build, config=None)
        yoke_config = service_cfg['yoke']
        # Get the working directory of the service yoke is handling.
        # Default to the current directory.
        yoke_working_dir = os.path.abspath(
            yoke_config.get('working_dir', '.'))
        env_dict = yoke_config.get('environment', {})
        # Let the `yoke` config section override the stage.
        # There is a `yoke.stage` present, we use that instead of the stage
        # supplied to `yolo`.
        # Do this in case the yolo stage is not the same as the yoke stage.
        # For example, the yolo stage might be `dev`, but the yoke stage
        # might be `Development`.
        yoke_stage = yoke_config.get('stage', stage)
        config = YokeConfig(
            shellargs=args,
            project_dir=yoke_working_dir,
            stage=yoke_stage,
            env_dict=env_dict)
        args.config = config.get_config()
        # Directly hook into yoke
        yoke_build(args)

    def push(self, service, stage, bucket):
        """Push a local build of a Lambda service up into S3.

        This essentially readies the build to be deployed using
        `yolo deploy-lambda`.

        :param str service:
            Name of the service. See ``services`` in the yolo.yml file.
        :param str stage:
            Name of the stage for which the build has been created. The build
            will only be available for this stage.
        :param bucket:
            :class:`boto3.resources.factory.s3.Bucket` instance. Build
            artifacts will be pushed into this bucket.
        """
        service_cfg = self.yolo_file.services[service]
        if not service_cfg['type'] in (
                yolo_file.YoloFile.SERVICE_TYPE_LAMBDA,
                yolo_file.YoloFile.SERVICE_TYPE_LAMBDA_APIGATEWAY,
        ):

            # Nothing to do if it's not a Lambda-type service.
            raise yolo.exceptions.YoloError(
                'Service "{}" is not a Lambda service.'.format(service)
            )

        print('pushing build for lambda service "{}"...'.format(service))

        bucket_folder_prefix = const.BUCKET_FOLDER_PREFIXES['stage-build'].format(
            stage=stage,
            service=service,
            sha1=utils.get_version_hash(),
            timestamp=utils.now_timestamp(),
        )

        lambda_fn_path = os.path.join(
            os.path.abspath(service_cfg['dist_path']),
            'lambda_function.zip'
        )
        bucket.upload_file(
            Filename=lambda_fn_path,
            Key=os.path.join(bucket_folder_prefix, 'lambda_function.zip'),
            Callback=utils.S3UploadProgress(lambda_fn_path),
        )
        # if apig, upload service.working_dir + 'swagger.yml' to 'swagger.yaml'
        if service_cfg['type'] == (
                yolo_file.YoloFile.SERVICE_TYPE_LAMBDA_APIGATEWAY
        ):
            # grab the rendered swagger file from the working_dir
            # and upload it to the S3 bucket
            swagger_yaml_path = os.path.join(
                service_cfg['yoke']['working_dir'], 'swagger.yml'
            )
            bucket.upload_file(
                Filename=swagger_yaml_path,
                Key=os.path.join(bucket_folder_prefix, const.SWAGGER_YAML),
            )

        bucket.upload_fileobj(
            Fileobj=self.yolo_file.to_fileobj(),
            # Name the file `yolo.yaml` no matter what it is on the local file
            # system. This allows for later deployments to just pick the file
            # up (or ignore it explicitly) by convention.
            Key=os.path.join(bucket_folder_prefix, const.YOLO_YAML),
        )

    def deploy_local_version(self, service, stage):
        """Deploy a Lambda service from a local ZIP file.

        :param str service:
            Name of the service. See ``services`` in the yolo.yml file.
        :param str stage:
            Stage to deploy to.
        """
        print('Deploying {service} from local to stage "{stage}"...'.format(
            service=service, stage=stage
        ))
        service_cfg = self.yolo_file.services[service]
        lambda_fn_cfg = service_cfg['lambda_function_configuration']
        lambda_fn_path = os.path.join(
            os.path.abspath(service_cfg['dist_path']),
            'lambda_function.zip'
        )
        with open(lambda_fn_path, 'rb') as fp:
            local_zip_contents = fp.read()
        code_config = dict(ZipFile=local_zip_contents)
        # The build yolo file is the same as the local copy, so just use that.
        self.build_yolo_file = self.yolo_file

        # Create a new lambda function version. If the lambda function already
        # exists, just create a new version of the function. If the function
        # doesn't exists, create the function.
        fn_version = self._create_or_update_lambda_function(
            service, stage, lambda_fn_cfg, code_config
        )
        # With the newly uploaded lambda function version, create an alias for
        # the function using the supplied stage for the alias name.
        self._create_lambda_alias_for_stage(
            lambda_fn_cfg['FunctionName'], fn_version, stage
        )

        # Once the lambda deployment is done, finish wiring it up to API
        # Gateway, if applicable.
        if service_cfg['type'] == yolo_file.YoloFile.SERVICE_TYPE_LAMBDA_APIGATEWAY:
            swagger_yaml_path = os.path.join(
                service_cfg['yoke']['working_dir'], 'swagger.yml'
            )
            with open(swagger_yaml_path, 'r') as fp:
                swagger_contents = fp.read()

            self._deploy_api(service, stage, swagger_contents)

    def deploy(self, service, stage, version, bucket):
        """Deploy a Lambda service from an existing build.

        :param str service:
            Name of the service. See ``services`` in the yolo.yml file.
        :param str stage:
            Stage to deploy to.
        :param str version:
            Version of the ``service`` to deploy to the given ``stage``.
        :param bucket:
            :class:`boto3.resources.factory.s3.Bucket` instance. Search for
            pushed builds in this bucket which match the ``service`` and
            ``stage`` parameters.
        """
        print(
            'Deploying {service} version {version} '
            'to stage "{stage}"...'.format(
                service=service, stage=stage, version=version
            )
        )
        service_cfg = self.yolo_file.services[service]

        bucket_folder_prefix = (
            const.BUCKET_FOLDER_PREFIXES['stage-build-by-version'].format(
                stage=stage,
                service=service,
                sha1=version,
            )
        )

        s3_client = self.faws_client.aws_client(
            self.context.account.account_number,
            's3',
            region_name=self.context.stage.region,
        )

        # TODO(larsbutler): Support fetching builds list given a partial
        # sha1/build version ID, but make sure that we disambiguate.
        # Get the latest build for this version:
        builds = self._get_builds_list(
            service, stage, bucket.name, version=version
        )

        if not builds:
            raise yolo.exceptions.YoloError(
                'No builds found for version "{}".'.format(version)
            )

        latest_build = sorted(builds, key=lambda x: x[1], reverse=True)[0]
        sha1, timestamp = latest_build

        # Pull down the yolo.yaml from the build folder in S3:
        build_yolo_yaml_s3_path = os.path.join(
            bucket_folder_prefix, timestamp, const.YOLO_YAML
        )

        fp, temp_yolo_yaml_path = tempfile.mkstemp()
        try:
            with open(temp_yolo_yaml_path, 'wb') as temp_yolo_yaml:
                s3_client.download_fileobj(
                    bucket.name, build_yolo_yaml_s3_path, temp_yolo_yaml
                )
            # Read it as a YoloFile object and render any variables from current
            # infrastructure stacks:
            self.build_yolo_file = yolo_file.YoloFile.from_path(
                temp_yolo_yaml_path
            ).render(**self.context)
        finally:
            # Clean up the temp file:
            os.remove(temp_yolo_yaml_path)
            os.close(fp)

        lambda_fn_cfg = self.build_yolo_file.services[service][
            'lambda_function_configuration'
        ]

        # Build code configuration dictionary for the target release (which is
        # stored in S3).
        code_config = self._get_code_config(
            bucket.name, bucket_folder_prefix, timestamp
        )

        # Create a new lambda function version. If the lambda function already
        # exists, just create a new version of the function. If the function
        # doesn't exists, create the function.
        # The returned function version is a incrememtal version number tracked
        # by Lambda (e.g., 123, 7).
        fn_version = self._create_or_update_lambda_function(
            service, stage, lambda_fn_cfg, code_config
        )
        # With the newly uploaded lambda function version, create an alias for
        # the function using the supplied stage for the alias name.
        self._create_lambda_alias_for_stage(
            lambda_fn_cfg['FunctionName'], fn_version, stage
        )

        # Once the lambda deployment is done, finish wiring it up to API
        # Gateway, if applicable.
        if service_cfg['type'] == yolo_file.YoloFile.SERVICE_TYPE_LAMBDA_APIGATEWAY:
            # Download the Swagger API definition from the build location:
            swagger_s3_key = os.path.join(
                bucket_folder_prefix, timestamp, const.SWAGGER_YAML
            )
            # Read the Swagger contents and prepare to upload them to API
            # Gateway:
            swagger_temp_file = utils.StringIO()
            try:
                s3_client.download_fileobj(
                    bucket.name,
                    swagger_s3_key,
                    swagger_temp_file,
                )
                swagger_temp_file.seek(0)
                swagger_contents = swagger_temp_file.read()
            finally:
                swagger_temp_file.close()

            self._deploy_api(service, stage, swagger_contents)

    def _get_code_config(self, bucket_name, bucket_folder_prefix, timestamp):
        """Build a "code config" dictionary for the Lambda function.

        :param str bucket_name:
            Name of the S3 bucket where code builds are stored.
        :param str bucket_folder_prefix:
            Pseudo-folder prefix where all builds for a given
            service/stage/version are stored.
        :param str timestamp:
            Timestamp of a specific build.

        :returns:
            `dict` containing either 'S3Bucket' and 'S3Key' keys, or a
            'ZipFile' key. These parameters are used specifically to create AWS
            Lambda functions or update code for an existing one.
            See https://docs.aws.amazon.com/lambda/latest/dg/API_UpdateFunctionCode.html.
        """
        s3_client = self.faws_client.aws_client(
            self.context.account.account_number,
            's3',
            region_name=self.context.stage.region,
        )

        # By default, try to just reference lambda code from the build location
        # in S3:
        code_config = dict(
            S3Bucket=bucket_name,
            S3Key=os.path.join(
                bucket_folder_prefix, timestamp, 'lambda_function.zip'
            ),
        )

        # However, if the build bucket is not in the same region as the
        # stage config, we need to work around this.
        bucket_loc = s3_client.get_bucket_location(
            Bucket=bucket_name
        )['LocationConstraint']
        if not bucket_loc == self.context.stage.region:
            # TODO(larsbutler): Split the yolo builds bucket into
            # region-specific buckets. This will avoid the cross-region issue
            # noted below.
            LOG.warning(
                'The build artifacts are in a different region from the '
                'deployment target. This will require additional local '
                'copying.'
            )

            # You can't point a Lambda function to code which exists in a
            # different region in S3. As a work around, we can download the
            # code locally and then upload it directly to Lambda. This could be
            # wasteful in terms of bandwidth, but at least it works. This needs
            # more thought.

            # Download the zip file
            temp_zipfile = utils.StringIO()
            try:
                s3_client.download_fileobj(
                    code_config['S3Bucket'],
                    code_config['S3Key'],
                    temp_zipfile,
                )
                temp_zipfile.seek(0)
                code_config = dict(ZipFile=temp_zipfile.read())
            finally:
                temp_zipfile.close()
        return code_config

    def _create_or_update_lambda_function(self, service, stage, lambda_fn_cfg,
                                          code_config):
        """Create or update the target Lambda function and return its ID.

        Update the Lambda function code as well as the configuration (memory,
        timeout, etc.).

        :param str service:
            Name of the service. See ``services`` in the yolo.yml file.
        :param str stage:
            Stage to deploy to.
        :param dict lambda_fn_cfg:
            Target service's ``lambda_function_configuration`` section, from
            the yolo.yml file.
        :param dict code_config:
            Dictionary containing Lambda function code configuration details.
            It must contain either the keys ``S3Bucket`` and ``S3Key`` to point
            to a code zipfile location in S3, or it must contain a ``ZipFile``
            key, the value of which should be a byte string of the zipfile
            contents (such as that returned by a ``open('file.zip').read()``).
        """
        lambda_client = self.aws_client(
            self.context.account.account_number,
            'lambda',
            region_name=self.context.stage.region,
        )

        # Set the appropriate config pointer for SSM in the lambda
        # envvars. To do that we need to do a couple of things:
        # 1. Get latest lambda function version
        # 2. Increment that by 1 (or just use 1 the function doesn't exist yet)
        fn_versions = self._get_all_lambda_fn_versions(
            lambda_fn_cfg['FunctionName'],
        )
        if len(fn_versions) == 0:
            next_fn_version = 1
        else:
            next_fn_version = max(fn_versions) + 1
        LOG.info('New Lambda function version will be %s', next_fn_version)
        # 3. Copy SSM parameters from /service/stage/latest to
        #    /service/stage/<next_fn_version>
        self._copy_ssm_parameters(service, stage, next_fn_version)
        # 4. Set a Lambda function envvar to point to this config version:
        if 'Environment' not in lambda_fn_cfg:
            # 'Environment' is optional, so add it here if there wasn't one
            # defined in the yolofile.
            lambda_fn_cfg['Environment'] = {'Variables': {}}
        # TODO(larsbutler): Check for envvar conflicts. It's unlikely that
        # someone will name a variable SSM_CONFIG_VERSION, but you never know.
        lambda_fn_cfg['Environment']['Variables'][const.SSM_CONFIG_VERSION] = (
            '/{service}/{stage}/{version}/'.format(
                service=service, stage=stage, version=next_fn_version
            )
        )
        # NOTE(larsbutler): Because all of these operations are not atomic,
        # there is the slight chance that two developers working on the same
        # service in the same account could clobber each other's config.
        # There's not a trivial way to work around that, though, and the risk
        # is small so we'll just keep it documented here for now and solve it
        # when it becomes a problem.

        # Call Lambda API and check if function exists and create or update the
        # function.
        fn_version = None
        try:
            lambda_client.get_function(
                FunctionName=lambda_fn_cfg['FunctionName']
            )
        except botocore.exceptions.ClientError as err:
            if 'ResourceNotFoundException' in str(err):
                print('Function "{}" does not exist. Creating...'.format(
                    lambda_fn_cfg['FunctionName']
                ))
                # Function doesn't exist; create function with code+config
                fn_version = lambda_client.create_function(
                    Code=code_config,
                    Publish=True,
                    **lambda_fn_cfg
                )['Version']
                print('Function "{}" created (version "{}").'.format(
                    lambda_fn_cfg['FunctionName'],
                    fn_version,
                ))
            else:
                # An unexpected error occurred.
                raise
        else:
            # Function exists; update code and config.
            print('Function "{}" already exists. Updating...'.format(
                lambda_fn_cfg['FunctionName']
            ))
            lambda_client.update_function_code(
                FunctionName=lambda_fn_cfg['FunctionName'],
                **code_config
            )
            lambda_client.update_function_configuration(
                **lambda_fn_cfg
            )
            # Now that code and configuration are in place for $LATEST, we can
            # publish a new version that can be referenced.
            # NOTE(szilveszter): If two users are publishing Lambda functions
            # at the same time, there can be a race condition, because
            # code update, config update, and publishing aren't atomic
            # operations. We can update the code and publish it as an atomic
            # operation, but then we can't update the config (it can only be
            # updated for the $LATEST version).
            fn_version = lambda_client.publish_version(
                FunctionName=lambda_fn_cfg['FunctionName'],
            )['Version']
            print('Function "{}" updated (version "{}").'.format(
                lambda_fn_cfg['FunctionName'],
                fn_version,
            ))

        # As a sanity check, compare fn_version below and next_fn_version and
        # raise an error if they don't match. The deployments aren't atomic,
        # so it's a good idea to let the user know that a race condition was
        # encountered.
        if not int(fn_version) == int(next_fn_version):
            raise yolo.exceptions.YoloError(
                'Invalid deployed function version! Expected: '
                '{next_fn_version}. Got: {fn_version}. Probable cause: '
                'Another deployment of this service on this exact stage '
                'occurred at the same time and clobbered something. '
                'Please try again.'.format(
                    next_fn_version=next_fn_version,
                    fn_version=fn_version,
                )
            )
        return fn_version

    def _get_all_lambda_fn_versions(self, lambda_fn_name):
        """Get a list of all function versions for a given function.

        NOTE: $LATEST is excluded from the list.

        :param str lambda_fn_name:
            Unique name of a Lambda function.

        :returns:
            `list` of `int` values representing all Lambda function versions.
        """
        lambda_client = self.faws_client.aws_client(
            self.context.account.account_number,
            'lambda',
            region_name=self.context.stage.region,
        )

        max_items = 1000
        fn_versions = []

        def _fetch_fn_version_page(marker=None):
            kwargs = dict(
                FunctionName=lambda_fn_name,
                # NOTE(larsbutler, 13-Sep-2017): I discovered a bug in the
                # Lambda API whereby there is an artificial maximum of 50
                # MaxItems returned by this API endpoint. I've filed a support
                # issue with AWS. Until that's fixed, this API call will return
                # no more than 50 items, regardless of what we pass here for
                # `MaxItems`.
                MaxItems=max_items,
            )
            if marker is not None:
                kwargs['Marker'] = marker

            fn_versions_resp = lambda_client.list_versions_by_function(
                **kwargs
            )
            marker = fn_versions_resp.get('NextMarker')
            versions = fn_versions_resp['Versions']
            for version in versions:
                version_num = version['Version']
                if version_num == '$LATEST':
                    # Skip this; if the function exists, there's always a
                    # $LATEST version. We don't care about this.
                    continue
                else:
                    fn_versions.append(int(version_num))
            return marker

        try:
            # Get the first page:
            marker = _fetch_fn_version_page()

            # Fetch any additional pages (if applicable):
            while marker is not None:
                marker = _fetch_fn_version_page(marker=marker)
        except botocore.exceptions.ClientError as exc:
            if '(ResourceNotFoundException)' in str(exc):
                print('No existing versions found for {}.'.format(
                    lambda_fn_name))
            else:
                raise exc

        return fn_versions

    def _copy_ssm_parameters(self, service, stage, deploy_version):
        """Copy parameters stored in SSM to a target namespace in SSM.

        :param str service:
            Name of the service. See ``services`` in the yolo.yml file.
        :param str stage:
            Stage to deploy to.
        :param int deploy_version:
            An incremental deployment version number. Every successful
            deployment for a given service+stage should incremental the version
            number.
        """
        # Source SSM Parameter Store path
        source_path = '/{service}/{stage}/latest/'.format(
            service=service, stage=stage
        )
        # Destination SSM Parameter Store path
        dest_path = '/{service}/{stage}/{deploy_version}/'.format(
            service=service, stage=stage, deploy_version=deploy_version
        )
        ssm_client = self.aws_client(
            self.context.account.account_number,
            'ssm',
            region_name=self.context.stage.region,
        )

        ssm_params = []

        def _fetch_param_page(next_token=None):
            kwargs = dict(
                Path=source_path,
                WithDecryption=True,
                MaxResults=10,
            )
            if next_token is not None:
                kwargs['NextToken'] = next_token
            params_resp = ssm_client.get_parameters_by_path(**kwargs)
            next_token = params_resp.get('NextToken')
            ssm_params.extend(params_resp['Parameters'])
            return next_token

        # Get the first page:
        next_token = _fetch_param_page()

        # Fetch any additional pages:
        while next_token is not None:
            next_token = _fetch_param_page(next_token=next_token)

        # Check that all parameters defined in the build_yolofile are present
        # in the SSM parameter store.
        param_names = set(x['Name'].split(source_path)[1] for x in ssm_params)

        service_cfg = self.build_yolo_file.services[service]

        # If there are no paramters defined in the yolo.yaml, we can skip
        # parameter checking and copying entirely.
        if 'parameters' in service_cfg:
            # Get stage specific parameter config, or get the default if this
            # is an ad-hoc/custom stage.
            build_yolofile_params = service_cfg['parameters']['stages'].get(
                # TODO(larsbutler): handle index errors if no default defined.
                stage, service_cfg['parameters']['stages']['default']
            )
            build_yolofile_param_names = set(
                x['name'] for x in build_yolofile_params
            )

            # If the parameters needed for the deployment are not available in
            # SSM, throw a helpful error.
            missing_params = build_yolofile_param_names.difference(param_names)
            if missing_params:
                raise yolo.exceptions.YoloError(
                    'The following parameters were not available for '
                    'deployment: {missing_params}. To fix this, try running '
                    '`yolo put-parameters ' '--service {service} '
                    '--stage {stage}`.'.format(
                        missing_params=', '.join(sorted(missing_params)),
                        service=service,
                        stage=stage,
                    )
                )

            # Now copy those params to the target path:
            for param in ssm_params:
                new_param_name = os.path.join(
                    dest_path, os.path.basename(param['Name'])
                )
                LOG.info('Copying parameter %(old)s to %(new)s',
                         dict(old=param['Name'], new=new_param_name))
                ssm_client.put_parameter(
                    Name=new_param_name,
                    Type=param['Type'],
                    Value=param['Value'],
                    # NOTE(larsbutler): We overwrite here just in case a deploy
                    # command was executed but was aborted before the Lambda
                    # function was deployed and the function version
                    # incremented. This ensures that we're able to move
                    # forward, even if that means slightly clobbering
                    # something. In the worst case, we can run the deployment
                    # again and everything will get cleaned up.
                    Overwrite=True,
                )

    def _create_lambda_alias_for_stage(self, lambda_fn_name, fn_version,
                                       stage):
        """Create or update a Lambda function alias for the given ``stage``.

        Do so by mapping a Lambda function version to the given ``stage``.

        :param str lambda_fn_name:
            Name of the target Lambda function.
        :param str fn_version:
            Version of the Lambda function to map to the alias specified by
            ``stage``.
        :param str stage:
            Target stage name. In this case, ``stage`` indicates the Lambda
            function alias name.
        """
        lambda_client = self.faws_client.aws_client(
            self.context.account.account_number,
            'lambda',
            region_name=self.context.stage.region,
        )
        try:
            lambda_client.get_alias(
                FunctionName=lambda_fn_name,
                Name=stage,
            )
        except botocore.exceptions.ClientError as err:
            if 'ResourceNotFoundException' in str(err):
                print(
                    'Function alias for stage "{}" does not exist. '
                    'Creating...'.format(stage)
                )
                lambda_client.create_alias(
                    FunctionName=lambda_fn_name,
                    Name=stage,
                    FunctionVersion=fn_version,
                )
                print('Function alias for stage "{}" created.'.format(stage))
            else:
                # An unexpected error occurred.
                raise
        else:
            print(
                'Function alias for stage "{}" already exists. '
                'Updating...'.format(stage)
            )
            lambda_client.update_alias(
                FunctionName=lambda_fn_name,
                Name=stage,
                FunctionVersion=fn_version,
            )
            print('Function alias for stage "{}" updated.'.format(stage))

    def _deploy_api(self, service, stage, swagger_contents):
        """Wire up the Lambda function to API Gateway.

        :param str service:
            Name of the service. See ``services`` in the yolo.yml file.
        :param str stage:
            Stage to deploy to.
        :param str swagger_contents:
            Contents of the fully rendered Swagger definition that should be
            uploaded as the REST API.
        """
        # NOTE(larsbutler): When we update the REST API and are about to
        # create a deployment (linking the latest version of the REST API
        # to a particular stage), if someone else is testing API changes on
        # the same account and uploading to the same REST API ID, then the
        # APIs will clobber each other.
        # Possible solution: Parameterize the APIs based on stage name.
        service_cfg = self.yolo_file.services[service]
        apig_config = service_cfg['apigateway']
        apig_client = self.faws_client.aws_client(
            self.context.account.account_number,
            'apigateway',
            region_name=self.context.stage.region,
        )

        rest_api_name = apig_config['rest_api_name']
        rest_api_id = None

        # Create/update REST API:
        try:
            rest_api_id = self._get_rest_api_id(rest_api_name)
        except yolo.exceptions.ResourceNotFound:
            # Couldn't find the REST API; it probably doesn't exist yet.
            # Let's create it.
            print('Importing API "{}"...'.format(rest_api_name))
            rest_api_id = apig_client.import_rest_api(
                parameters=dict(basepath='prepend'),
                body=swagger_contents,
            )['id']
        else:
            # Rest API already exists; update it.
            print('Updating API "{}"...'.format(rest_api_name))
            apig_client.put_rest_api(
                restApiId=rest_api_id,
                mode='overwrite',
                body=swagger_contents,
                parameters=dict(basepath='prepend'),
            )

        # Deploy the API to the target stage:
        print('Deploying API to stage "{}"...'.format(stage))
        apig_client.create_deployment(
            restApiId=rest_api_id,
            stageName=stage,
        )

        print('Configuring API Gateway/Lambda base path mapping...')
        self._add_apig_lambda_base_path_mapping(service, stage)
        print('Done!')

    def _get_rest_api_id(self, rest_api_name):
        """Get the ID of a AWS::ApiGateway::RestApi resource, give its name.

        :param str rest_api_name:
            Name of an existing REST API in API Gateway.

        :returns:
            ID of the REST API as a string.
        :raises:
            :class:`yolo.exceptions.ResourceNotFound` if the given REST API
            does not exist.
        """
        apig_client = self.faws_client.aws_client(
            self.context.account.account_number,
            'apigateway',
            region_name=self.context.stage.region,
        )
        rest_api_id = None
        # FIXME(larsbutler): A limit of 500 shouldn't be a problem, but you
        # never know.
        rest_apis = apig_client.get_rest_apis(limit=500)
        for rest_api in rest_apis['items']:
            if rest_api['name'] == rest_api_name:
                rest_api_id = rest_api['id']
                break
        else:
            raise yolo.exceptions.ResourceNotFound(
                'Unable to find REST API "{}"'.format(rest_api_name)
            )
        return rest_api_id

    def _add_apig_lambda_base_path_mapping(self, service, stage):
        """Add all relevant Lambda/API Gateway base path mappings.

        Base path mappings are defined in the yolo.yml file in a service's
        ``apigateway`` section. There can be one or more mappings defined.

        :param str service:
            The name of the service being configured.
        :param str stage:
            Stage for which to perform this base path mapping.
        """
        service_cfg = self.yolo_file.services[service]
        stage_cfg = self.yolo_file.get_stage_config(stage)

        apigateway_configs = service_cfg['apigateway']
        apig_client = self.faws_client.aws_client(
            self.context.account.account_number,
            'apigateway',
            region_name=stage_cfg['region'],
        )
        if isinstance(apigateway_configs, dict):
            apigateway_configs = [apigateway_configs]

        for apigateway_config in apigateway_configs:
            rest_api_id = self._get_rest_api_id(
                apigateway_config['rest_api_name']
            )
            yoke_stage = service_cfg['yoke'].get('stage', stage)
            # Add base path mapping
            base_path = apigateway_config['base_path'].strip()
            domain_name = apigateway_config['custom_domain']
            if domain_name == '':
                # This is an easy way to let us know the domain does not exist for
                # the given stage, so let's skip base path mapping creation.
                print('Domain name is empty, skipping base path mapping.')
                return
            if base_path == '/':
                # This is the default base path, but you shouldn't specify it
                # explicitly.
                # If this base path is specified, change it to empty string in
                # order to achieve the same result.
                base_path = ''
            existing_base_paths = [
                # For some strange reason the API-G API returns the string `(none)`
                # if the base mapping is for `/`.
                item['basePath'].replace('(none)', '')
                for item in
                apig_client.get_base_path_mappings(
                    domainName=domain_name,
                    limit=500,
                ).get('items', [])
            ]
            if base_path in existing_base_paths:
                # If the base path mapping is set up correctly, we don't have to do
                # anything.
                base_path_mapping = apig_client.get_base_path_mapping(
                    domainName=domain_name,
                    # That strange behavior again: if base path would be empty, we
                    # have to address it as `(none)`.
                    basePath=base_path or '(none)',
                )
                if (
                    base_path_mapping['restApiId'] != rest_api_id or
                    base_path_mapping['stage'] != yoke_stage
                ):
                    # TODO(szilveszter): If the base path mapping was changed, we
                    # have to warn the user about this, because unfortunately the
                    # API for updating base path mappings doesn't work yet.
                    print(
                        'Base path mapping has to be updated, but action cannot be '
                        'performed via the API, you have to use the AWS Console.'
                    )
                else:
                    print('Base path mapping already in place, no update needed.')
            else:
                # We have to create a new base path mapping from scratch
                apig_client.create_base_path_mapping(
                    domainName=domain_name,
                    basePath=base_path,
                    restApiId=rest_api_id,
                    # It's important to take the `yoke` stage here, since it may vary
                    # from the `yolo` stage.
                    stage=yoke_stage,
                )
                print(
                    'Created base path mapping of {domain} to '
                    '{rest_api_name}:{stage}'.format(
                        domain=domain_name,
                        rest_api_name=apigateway_config['rest_api_name'],
                        stage=yoke_stage,
                    )
                )

    def show(self, service, stage):
        """Show configuration details of a service for a given stage.

        :param str service:
            The name of the service being shown.
        :param str stage:
            The name of the stage for which to show configuration.
        """
        lambda_client = self.faws_client.aws_client(
            self.context.account.account_number,
            'lambda',
            region_name=self.context.stage.region,
        )
        func_name = self.yolo_file.services[service][
            'lambda_function_configuration'
        ]['FunctionName']
        func_config = lambda_client.get_function(
            FunctionName=func_name,
            # Get the exact function and version associated with the
            # alias=stage.
            Qualifier=stage,
        )['Configuration']

        table = [
            ('Attribute', 'Value'),
            ('Name', func_name),
        ]
        attrs = [
            'Description',
            'Version',
            'Runtime',
            'Timeout',
            'MemorySize',
        ]
        for attr in attrs:
            table.append((attr, func_config[attr]))

        # Show a separate table for environment variables:
        env_vars = func_config['Environment']['Variables']
        for key, value in sorted(env_vars.items()):
            table.append((key, value))

        print(tabulate.tabulate(table, headers='firstrow', tablefmt='simple'))
