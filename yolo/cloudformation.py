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
                     tags, asynchronous=False):
        # Create stack
        result = self._cf.create_stack(
            StackName=stack_name,
            Parameters=stack_params,
            TemplateURL=master_url,
            Capabilities=[self.CF_CAPABILITY_IAM, self.CF_CAPABILITY_NAMED_IAM],
            Tags=tags,
        )
        print('creating stack "{}"...'.format(result['StackId']))
        if not asynchronous:
            create_waiter = VerboseCloudFormationWaiter(self._cf, 'stack_create_complete')
            create_waiter.wait(StackName=stack_name)
            print('stack "{}" created.'.format(stack_name))

    def update_stack(self, stack_name, master_url, stack_params,
                     asynchronous=False):
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
                       tags, stack_details, asynchronous=False):
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
                          tags, asynchronous=asynchronous)

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
