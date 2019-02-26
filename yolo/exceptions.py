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


class YoloError(Exception):
    """Errors meant to be displayed to the user in a friendly way."""


class NoInfrastructureError(YoloError):
    pass


class StackDoesNotExist(Exception):
    pass


class ResourceNotFound(Exception):
    pass


class CredentialsError(YoloError):
    """Errors related to the fetching or handling of credentials."""


class CloudFormationError(Exception):
    """Errors related to CloudFormation resource management."""
