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

import yolo

YOLO_YAML = 'yolo.yaml'
DEFAULT_FILENAMES = [YOLO_YAML, 'yolo.yml']
RACKSPACE_USERNAME = 'RACKSPACE_USERNAME'
RACKSPACE_API_KEY = 'RACKSPACE_API_KEY'
AWS_PROFILE_NAME = 'AWS_PROFILE_NAME'
NAMESPACE = 'yolo'
SWAGGER_YAML = 'swagger.yaml'
# FAWS account service level IDs and their respective human-readable labels.
ACCT_SVC_LVL_MAPPING = {
    '902610ef3e2748a4a6a20866323e1774': 'Aviator',
    '439cc3a473744806be5d37fccdfb4304': 'Navigator',
    'd9a9635904d742a9b3d0f31575a81e0f': 'Digital',
}
BUCKET_NOT_FOUND = (
    'An error occurred (404) when calling the HeadBucket operation: Not Found'
)
YOLO_STACK_TAGS = {
    'created-with-yolo-version': dict(
        Key='yolo:CreatedWithVersion', Value=yolo.__version__,
    ),
    'protected': dict(Key='yolo:Protected', Value='true'),
}
BUCKET_FOLDER_PREFIXES = {
    'account-templates': 'templates/account/{timestamp}',
    'stage-templates': 'templates/stages/{stage}/{timestamp}',
    'stage-builds': 'builds/stages/{stage}/services/{service}',
    'stage-build': (
        'builds/stages/{stage}/services/{service}/{sha1}/{timestamp}'
    ),
    'stage-build-by-version': (
        'builds/stages/{stage}/services/{service}/{sha1}'
    ),
}
# Environment variable name for storing SSM config version/path.
# This tells deployed applications where to find config/secrets in SSM.
SSM_CONFIG_VERSION = 'SSM_CONFIG_VERSION'
S3_UPLOAD_EXTRA_ARGS = dict(ACL='private', ServerSideEncryption='AES256')

API_GATEWAY_INTEGRATION_AWS = 'AWS'
