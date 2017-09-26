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

import botocore.client
import tabulate

from yolo import const


class BaseService(object):
    """Base class for abstractions of service commands."""
    DEFAULT_TIMEOUT = 60

    def __init__(self, yolo_file, faws_client, context,
                 timeout=DEFAULT_TIMEOUT):
        """
        :param yolo_file:
            :class:`yolo.yolo_file.YoloFile` instance.
        :param faws_client:
            :class:`yolo.faws_client.FAWSClient` instance.
        :param context:
            :class:`yolo.utils.DottedDict` representing the current command
            context.
        :param int timeout:
            Number of seconds the clients will wait for a response. In certain
            edge cases when network connection is slow, it's worth bumping this
            up.
        """
        self.yolo_file = yolo_file
        self.faws_client = faws_client
        self.context = context
        self.timeout = timeout

    def aws_client(self, acct_num, aws_service, region_name=None):
        config = botocore.client.Config(
            connect_timeout=60,
            read_timeout=self.timeout,
        )
        return self.faws_client.aws_client(acct_num, aws_service, region_name,
                                           config=config)

    def list_builds(self, service, stage, bucket):
        """List builds which have been pushed to S3 for a given service/stage.

        :param str service:
            Name of the service. See ``services`` in the yolo.yml file.
        :param str stage:
            List builds only for this stage.
        :param bucket:
            :class:`boto3.resources.factory.s3.Bucket` instance. Search for
            pushed builds in this bucket which match the ``service`` and
            ``stage`` parameters.
        """
        builds = self._get_builds_list(service, stage, bucket.name)
        if not builds:
            print('No builds found.')
            return

        # Sort builds, latest first:
        builds = sorted(builds, key=lambda x: x[1], reverse=True)
        headers = ['Build', 'Timestamp']
        table = [headers]
        table.extend(builds)
        print(tabulate.tabulate(table, headers='firstrow'))

    def _get_builds_list(self, service, stage, bucket_name, version=None):
        """Get a list of available builds for a given service/stage.

        :param str service:
            Name of the service. See ``services`` in the yolo.yml file.
        :param str stage:
            Get builds only for this stage.
        :param str bucket_name:
            Name of the S3 bucket in which to search for builds.

        :returns:
            A list of 2-tuple pairs of (version, timestamp) for each build.
        """
        s3_client = self.faws_client.aws_client(
            self.context.account.account_number,
            's3',
            region_name=self.context.stage.region,
        )
        if version is None:
            bucket_folder_prefix = const.BUCKET_FOLDER_PREFIXES['stage-builds'].format(
                stage=stage, service=service
            ) + '/'
        else:
            bucket_folder_prefix = (
                const.BUCKET_FOLDER_PREFIXES['stage-build-by-version'].format(
                    stage=stage,
                    service=service,
                    sha1=version,
                )
            )

        s3_result = s3_client.list_objects_v2(
            Bucket=bucket_name,
            Prefix=bucket_folder_prefix,
            Delimiter='/' + const.YOLO_YAML,
        )

        builds = []
        if version is None:
            for common_prefix in s3_result.get('CommonPrefixes', []):
                build_details = common_prefix['Prefix'].strip(const.YOLO_YAML).split(
                    bucket_folder_prefix
                )[1].split('/')
                if not len(build_details) == 3:
                    # Ignore it; it's not a valid build.
                    continue
                else:
                    sha1, timestamp, _ = build_details
                    builds.append((sha1, timestamp))
        else:
            for common_prefix in s3_result.get('CommonPrefixes', []):
                build_details = common_prefix['Prefix'].rsplit('/', 3)
                if not len(build_details) == 4:
                    # Ignore it; it's not a valid build.
                    continue
                else:
                    _, sha1, timestamp, _ = build_details
                    builds.append((sha1, timestamp))
        return builds
