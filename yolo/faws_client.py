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

import boto3
import requests

from yolo import const
import yolo.exceptions


RAX_IDENTITY_ENDPOINT = 'https://identity.api.rackspacecloud.com'


def authenticate(username, apikey):
    """Authenticate to Rackspace Cloud Identity.

    :param str username:
        Rackspace username.
    :param str apikey:
        Rackspace API key.

    :returns:
        2-tuple of (rackspace_account_number, rackspace_auth_token).
        Use these values as X-Tenant-Id and X-Auth-Token header values
        (respectively) when making calls to Rackspace APIs.
    """
    auth_params = {
        "auth": {
            "RAX-KSKEY:apiKeyCredentials": {
                "username": username,
                "apiKey": apikey,
            }
        }
    }
    response = requests.post(
        RAX_IDENTITY_ENDPOINT + '/v2.0/tokens',
        json=auth_params,
        headers={'Content-Type': 'application/json'},
    )
    response.raise_for_status()
    resp_data = response.json()
    # Rackspace account number
    x_tenant_id = resp_data['access']['token']['tenant']['id']
    # Rackspace auth token
    x_auth_token = resp_data['access']['token']['id']
    return x_tenant_id, x_auth_token


class FAWSClient(object):
    """Basic client for talking to the Fanatical AWS API."""

    FAWS_API_ENDPOINT = 'https://accounts.api.manage.rackspace.com'

    def __init__(self, username, apikey):
        self.username = username
        self.apikey = apikey

        # API tenant ID/account ID and auth token, lazily loaded.
        self._x_tenant_id = None
        self._x_auth_token = None

        # AWS/Boto sessions for each account defined in `stages`
        self._boto3_sessions = {}

    @property
    def x_tenant_id(self):
        if self._x_tenant_id is None:
            self._x_tenant_id, self._x_auth_token = authenticate(
                self.username, self.apikey
            )
        return self._x_tenant_id

    @property
    def x_auth_token(self):
        if self._x_auth_token is None:
            self._x_tenant_id, self._x_auth_token = authenticate(
                self.username, self.apikey
            )
        return self._x_auth_token

    @property
    def request_headers(self):
        try:
            return {
                'X-Tenant-Id': self.x_tenant_id,
                'X-Auth-Token': self.x_auth_token,
                'Content-Type': 'application/json',
            }
        except requests.exceptions.HTTPError as err:
            if err.response.status_code == 401:
                # Bad credentials; raise a nice error message.
                raise yolo.exceptions.YoloError(
                    'Invalid credentials: Run `yolo login` or check your '
                    '"{}" and "{}" environment variables.'.format(
                        const.RACKSPACE_USERNAME, const.RACKSPACE_API_KEY
                    )
                )
            else:
                raise

    def _get(self, path):
        """
        :param str path:
            Request path. For example, /foo/bar (without the host).
        """
        return requests.get(
            self.FAWS_API_ENDPOINT + path,
            headers=self.request_headers,
        )

    def _post(self, path, body):
        """
        :param str path:
            Request path. For example, /foo/bar (without the host).
        :param dict body:
            JSON/Dict contents to send with the POST.
        """
        return requests.post(
            self.FAWS_API_ENDPOINT + path,
            json=body,
            headers=self.request_headers,
        )

    def get_aws_account_credentials(self, aws_account_number, duration=3600):
        """Get temporary AWS account credentials."""
        path = '/v0/awsAccounts/{}/credentials'.format(aws_account_number)
        body = dict(credential=dict(duration=duration))
        response = self._post(path, body)
        # We only need to explicitly handle 404s. Since yolo caches credentials
        # for talking to the FAWS API, it's possible the user might have the
        # wrong credentials cached (perhaps for another account).
        if response.status_code == 404:
            raise yolo.exceptions.YoloError(
                'Unknown account "{acct}" for username "{username}".\n'
                'Options:\n'
                '  - Verify the account number (`yolo list-accounts`).\n'
                '  - Log in with different credentials (`yolo login`).'.format(
                    acct=aws_account_number, username=self.username
                )
            )
        return response.json()

    def list_aws_accounts(self):
        response = self._get('/v0/awsAccounts')
        response.raise_for_status()
        return response.json()

    def boto3_session(self, acct_num):
        session = self._boto3_sessions.get(acct_num)
        if session is None:
            creds = self.get_aws_account_credentials(acct_num)
            cred = creds['credential']
            session = boto3.session.Session(
                aws_access_key_id=cred['accessKeyId'],
                aws_secret_access_key=cred['secretAccessKey'],
                aws_session_token=cred['sessionToken'],
            )
            self._boto3_sessions[acct_num] = session
        return session

    def aws_client(self, acct_num, aws_service, region_name=None, **kwargs):
        session = self.boto3_session(acct_num)
        return session.client(aws_service, region_name=region_name, **kwargs)
