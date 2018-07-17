import boto3
import requests

import yolo.credentials
import yolo.exceptions


class FAWSCredentialsProvider(yolo.credentials.AWSCredentials):

    RAX_IDENTITY_ENDPOINT = 'https://identity.api.rackspacecloud.com'
    FAWS_API_ENDPOINT = 'https://accounts.api.manage.rackspace.com'

    def __init__(self, rs_username, rs_api_key):
        self.rs_username = rs_username
        self.rs_api_key = rs_api_key

        # Lazy-load these only when requested.
        self._x_tenant_id = None
        self._x_auth_token = None
        self._session = None

    def _authenticate_rax(self):
        auth_params = {
            'auth': {
                'RAX-KSKEY:apiKeyCredentials': {
                    'username': self.rs_username,
                    'apiKey': self.rs_api_key,
                }
            }
        }
        response = requests.post(
            self.RAX_IDENTITY_ENDPOINT + '/v2.0/tokens'
            json=auth_params,
            headers={'Content-Type': 'application/json'},
        )
        response.raise_for_status()
        resp_data = response.json()
        self._x_tenant_id = resp_data['access']['token']['tenant']['id']
        self._x_auth_token = resp_data['access']['token']['id']

    @property
    def x_tenant_id(self):
        if self._x_tenant_id is None:
            self._authenticate_rax()
        return self._x_tenant_id

    @property
    def x_auth_token(self):
        if self._x_auth_token is None:
            self._authenticate_rax()
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

    def get_aws_account_credentials(self, aws_account_number):
        url = self.FAWS_API_ENDPOINT + '/v0/awsAccounts/{}/credentials'.format(
            aws_account_number
        )
        body = {'credential': {'duration': 3600}}  # 1 hour

        response = requests.post(
            url,
            headers=self.request_headers,
            json=body,
        )
        response.raise_for_status()
        creds = response.json()['credential']
        return yolo.credentials.AWSCredentials(
            aws_access_key_id=cred['accessKeyId'],
            aws_secret_access_key=cred['secretAccessKey'],
            aws_session_token=cred['sessionToken'],
        )

    def boto3_session(self, account_cfg):
        account_number = self.get_aws_account_number(account_cfg)
        creds = self.get_aws_account_credentials(account_number)
        if self._session is None:
            self._session = boto3.session.Session(
                aws_access_key_id=creds.aws_access_key_id,
                aws_secret_access_key=creds.aws_secret_access_key,
                aws_session_token=creds.aws_session_token,
            )
        return self._session

    def get_aws_account_number(self, account_cfg):
        return account_cfg['account_number']
