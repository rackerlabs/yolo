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

import os
import subprocess

import yolo
from yolo import const
import yolo.exceptions
import yolo.services
from yolo import utils
from yolo import yolo_file


class S3Service(yolo.services.BaseService):
    """Collection of functions for managing S3-based services."""

    def __init__(self, yolo_file, faws_client, context):
        """
        :param yolo_file:
            :class:`yolo.yolo_file.YoloFile` instance.
        :param faws_client:
            :class:`yolo.faws_client.FAWSClient` instance.
        :param context:
            :class:`yolo.utils.DottedDict` representing the current command
            context.
        """
        self.yolo_file = yolo_file
        self.faws_client = faws_client
        self.context = context

    def push(self, service, stage, bucket):
        """Push a local build of an S3-based service up into S3.

        This essentially readies the build to be deployed using
        `yolo deploy-s3`.

        :param str service:
            Name of the service. See ``services`` in the yolo.yml file.
        :param str stage:
            Name of the stage for which the build has been created. The build
            will only be available for this stage.
        :param bucket:
            :class:`boto3.resources.factory.s3.Bucket` instance. Build
            artifacts will be pushed into this bucket.
        """
        # TODO(larsbutler): check if the service exists, throw a nice error if
        # it doesn't.
        service_cfg = self.yolo_file.services[service]
        if not service_cfg['type'] == yolo_file.YoloFile.SERVICE_TYPE_S3:
            raise yolo.exceptions.YoloError(
                'Service "{}" is not an S3 service. Skipping.'.format(service)
            )

        print('creating build for S3 service "{}"...'.format(service))

        # TODO(larsbutler): try to avoid fetching extra sets of credentials. We
        # do this when constructing AWS clients, so there is some redundancy
        # here. This is definitely a performance due to the redudnant cred
        # fetching calls, but I haven't yet measured exactly what the impact
        # is.
        creds = self.faws_client.get_aws_account_credentials(
            self.context.account.account_number
        )
        cred = creds['credential']
        cred_vars = dict(
            AWS_ACCESS_KEY_ID=cred['accessKeyId'],
            AWS_SECRET_ACCESS_KEY=cred['secretAccessKey'],
        )
        if 'sessionToken' in cred:
            cred_vars['AWS_SESSION_TOKEN'] = cred['sessionToken']

        version_hash = utils.get_version_hash()
        bucket_folder_prefix = const.BUCKET_FOLDER_PREFIXES['stage-build'].format(
            stage=stage,
            service=service,
            sha1=version_hash,
            timestamp=utils.now_timestamp(),
        )

        # upload all of the other files associated with the build:
        source_path = os.path.abspath(service_cfg['build']['dist_dir'])
        dest_path = 's3://{bucket_name}/{folder_prefix}'.format(
            bucket_name=bucket.name,
            folder_prefix=bucket_folder_prefix,
        )
        sync_args = [
            'aws',
            's3',
            '--region',
            self.context.stage.region,
            'sync',
            '--delete',
            # Source
            source_path,
            # Destination
            dest_path,
        ]
        # Sync and delete files that exist in the destination but the
        # source.
        # For deploying static UI web applications, this is necessary in
        # order to "clean out" files from old deployments which have been
        # deleted, moved, etc.
        # '--delete',
        sp_env = os.environ.copy()
        sp_env.update(cred_vars)
        sp = subprocess.Popen(sync_args, env=sp_env)
        return_code = sp.wait()
        if return_code != 0:
            raise yolo.exceptions.YoloError(
                'Push to S3 failed, using command:\n{}'.format(
                    ' '.join(sync_args)
                )
            )

        # Upload the yolo.yaml file to assist with deploying the build later:
        bucket.upload_fileobj(
            Fileobj=self.yolo_file.to_fileobj(),
            # Name the file `yolo.yaml` no matter what it is on the local file
            # system. This allows for later deployments to just pick the file
            # up (or ignore it explicitly) by convention.
            Key=os.path.join(bucket_folder_prefix, const.YOLO_YAML),
            ExtraArgs=const.S3_UPLOAD_EXTRA_ARGS,
        )

        # TODO(larsbutler): check output and errors from the sp call
        print('Build {version_hash} pushed for service "{service}".'.format(
            version_hash=version_hash, service=service
        ))

    def deploy(self, service, stage, version, bucket):
        """Deploy an S3 service from an existing build.

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
        creds = self.faws_client.get_aws_account_credentials(
            self.context.account.account_number
        )

        cred = creds['credential']
        cred_vars = dict(
            AWS_ACCESS_KEY_ID=cred['accessKeyId'],
            AWS_SECRET_ACCESS_KEY=cred['secretAccessKey'],
        )
        if 'sessionToken' in cred:
            cred_vars['AWS_SESSION_TOKEN'] = cred['sessionToken']

        service_cfg = self.yolo_file.services[service]
        # NOTE(larsbutler): The infrastructure templates need to create this
        # bucket.
        target_bucket = service_cfg['bucket_name']

        # TODO(larsbutler): If an invalid version is given, it fails silently.
        # Add error handling to this.
        builds = self._get_builds_list(
            service, stage, bucket.name, version=version
        )

        if not builds:
            raise yolo.exceptions.YoloError(
                'No builds found for version "{}".'.format(version)
            )

        latest_build = sorted(builds, key=lambda x: x[1], reverse=True)[0]
        sha1, timestamp = latest_build

        source_prefix = const.BUCKET_FOLDER_PREFIXES['stage-build'].format(
            stage=stage,
            service=service,
            sha1=sha1,
            timestamp=timestamp,
        )
        source_path = 's3://{bucket}/{folder_prefix}'.format(
            bucket=bucket.name,
            folder_prefix=source_prefix,
        )
        dest_path = 's3://{}'.format(target_bucket)

        sync_args = [
            'aws',
            's3',
            '--region',
            self.context.stage.region,
            'sync',
            '--delete',
            # Source
            source_path,
            # Destination
            dest_path,
            # Don't deploy the yolo.yaml from the build location; this is a
            # build-only artifact and should NEVER go into production.
            '--exclude',
            const.YOLO_YAML,
        ]

        # Sync and delete files that exist in the destination but the
        # source.
        # For deploying static UI web applications, this is necessary in
        # order to "clean out" files from old deployments which have been
        # deleted, moved, etc.
        # '--delete',
        sp_env = os.environ.copy()
        sp_env.update(cred_vars)
        sp = subprocess.Popen(sync_args, env=sp_env)
        return_code = sp.wait()
        if return_code != 0:
            raise yolo.exceptions.YoloError(
                'Deploy to S3 failed, using command:\n{}'.format(
                    ' '.join(sync_args)
                )
            )
