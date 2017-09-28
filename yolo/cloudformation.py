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

import botocore.exceptions

from yolo.exceptions import StackDoesNotExist
from yolo import utils
from yolo.waiter import VerboseCloudFormationWaiter


class CloudFormation(object):
    CF_CAPABILITY_IAM = 'CAPABILITY_IAM'
    CF_CAPABILITY_NAMED_IAM = 'CAPABILITY_NAMED_IAM'

    def __init__(self, cf_client):
        self._cf = cf_client

    def stack_exists(self, stack_name):
        try:
            details = self._cf.describe_stacks(StackName=stack_name)
        except botocore.exceptions.ClientError as err:
            if 'does not exist' in str(err):
                print('stack "{}" does not exist'.format(stack_name))
                return False, {}
            else:
                # Something else went wrong
                raise
        else:
            return True, details

    def create_stack(self, stack_name, master_url, stack_params,
                     tags, asynchronous=False, protected=False):
        # Create stack
        create_stack_params = dict(
            StackName=stack_name,
            Parameters=stack_params,
            TemplateURL=master_url,
            Capabilities=[self.CF_CAPABILITY_IAM, self.CF_CAPABILITY_NAMED_IAM],
            Tags=tags,
        )
        if protected:
            create_stack_params['EnableTerminationProtection'] = True
        result = self._cf.create_stack(**create_stack_params)
        print('creating stack "{}"...'.format(result['StackId']))
        if not asynchronous:
            create_waiter = VerboseCloudFormationWaiter(self._cf, 'stack_create_complete')
            create_waiter.wait(StackName=stack_name)
            print('stack "{}" created.'.format(stack_name))

    def update_stack(self, stack_name, master_url, stack_params,
                     asynchronous=False, protected=False):
        if protected:
            # Add termination protection before anything:
            self._cf.update_termination_protection(
                EnableTerminationProtection=True,
                StackName=stack_name,
            )

        # Update the stack
        result = self._cf.update_stack(
            StackName=stack_name,
            Parameters=stack_params,
            TemplateURL=master_url,
            Capabilities=[self.CF_CAPABILITY_IAM, self.CF_CAPABILITY_NAMED_IAM],
        )
        print('updating stack "{}"...'.format(result['StackId']))
        if not asynchronous:
            update_waiter = VerboseCloudFormationWaiter(self._cf, 'stack_update_complete')
            update_waiter.wait(StackName=stack_name)
            print('stack "{}" updated.'.format(stack_name))

    def recreate_stack(self, stack_name, master_url, stack_params,
                       tags, stack_details, asynchronous=False,
                       protected=False):
        # Stack already exists. Delete it and recreate it.
        print('recreating stack "{}"...'.format(stack_name))
        print('deleting stack "{}"... (this may take a while)'.format(
            stack_name
        ))
        self._cf.delete_stack(StackName=stack_name)
        delete_waiter = VerboseCloudFormationWaiter(self._cf, 'stack_delete_complete')
        delete_waiter.wait(StackName=stack_name)
        print('stack "{}" has been deleted'.format(stack_name))
        self.create_stack(stack_name, master_url, stack_params,
                          tags, asynchronous=asynchronous, protected=protected)

    def create_change_set(self, stack_name, master_url, stack_params, tags):
        # change set name needs to be unique
        change_set_name = '{}-{}'.format(
            stack_name, utils.now_timestamp()
        ).replace('_', '-')

        result = self._cf.create_change_set(
            StackName=stack_name,
            ChangeSetName=change_set_name,
            TemplateURL=master_url,
            Parameters=stack_params,
            Tags=tags,
            Capabilities=[
                self.CF_CAPABILITY_IAM,
                self.CF_CAPABILITY_NAMED_IAM,
            ],
        )
        waiter = self._cf.get_waiter('change_set_create_complete')
        waiter.wait(
            ChangeSetName=change_set_name,
            StackName=stack_name,
            WaiterConfig={
                'Delay': 5,
                'MaxAttempts': 120,
            },
        )
        return result

    def get_stack_outputs(self, stack_name):
        try:
            response = self._cf.describe_stacks(StackName=stack_name)
        except botocore.exceptions.ClientError as exc:
            if 'does not exist' in str(exc):
                raise StackDoesNotExist()
            else:
                raise
        if 'Outputs' in response['Stacks'][0]:
            outputs = {
                output['OutputKey']: output['OutputValue']
                for output
                in response['Stacks'][0]['Outputs']
            }
        else:
            outputs = {}
        return outputs
