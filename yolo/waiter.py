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

import time

from botocore.exceptions import ClientError

import yolo.exceptions


class VerboseCloudFormationWaiter(object):
    """Custom waiter that prints on progress to standard outpout.

    This should help avoid build failures in certain CI systems for long-running
    stack creations/updates (especially when they involve CloudFront
    distributions).
    """
    WAITER_TYPE_CREATE = 'stack_create_complete'
    WAITER_TYPE_UPDATE = 'stack_update_complete'
    WAITER_TYPE_DELETE = 'stack_delete_complete'
    WAITER_TYPE_STATUS_MAP = {
        WAITER_TYPE_CREATE: 'CREATE_COMPLETE',
        WAITER_TYPE_UPDATE: 'UPDATE_COMPLETE',
        WAITER_TYPE_DELETE: 'DELETE_COMPLETE',
    }
    WAITER_TYPE_ACTION_MAP = {
        WAITER_TYPE_CREATE: 'creating',
        WAITER_TYPE_UPDATE: 'updating',
        WAITER_TYPE_DELETE: 'deleting',
    }
    POLL_INTERVAL = 30
    MAX_TRIES = 120

    def __init__(self, cf_client, waiter_type):
        self.cf_client = cf_client
        self.waiter_type = waiter_type

        if waiter_type not in self.WAITER_TYPE_STATUS_MAP.keys():
            raise ValueError('Unknown waiter type: {}'.format(waiter_type))

        self.target_status = self.WAITER_TYPE_STATUS_MAP[self.waiter_type]
        self.action = self.WAITER_TYPE_ACTION_MAP[self.waiter_type]

    def wait(self, **kwargs):
        tries = 0
        while True:
            tries += 1
            if tries > self.MAX_TRIES:
                # We have reached the max. number of retries, raising an
                # exception.
                raise RuntimeError('The stack operation took too long to complete.')
            try:
                response = self.cf_client.describe_stacks(**kwargs)
            except ClientError as exc:
                if (
                    'ValidationError' in str(exc) and
                    'does not exist' in str(exc) and
                    self.waiter_type == self.WAITER_TYPE_DELETE
                ):
                    # On a stack delete, if the stack doesn't exist anymore, it
                    # means success.
                    break
                else:
                    raise
            stack_status = response['Stacks'][0]['StackStatus']
            if stack_status == self.target_status:
                # All good!
                break
            elif 'IN_PROGRESS' in stack_status:
                # Still in progress, let's wait a bit, and retry
                time.sleep(self.POLL_INTERVAL)
                print('Still {} stack, please be patient...'.format(self.action))
                continue
            else:
                # This means we have reached an unexpected state, let's raise
                # an exception.
                raise yolo.exceptions.CloudFormationError(
                    'The stack reached an unexpected state: {}'.format(
                        stack_status
                    )
                )
