import os

import boto3

from yolo import credentials as creds
from yolo import exceptions


class AWSCredentialsProvider(object):

    def __init__(self, profile_name='default'):
        self._session = None
        self._profile_name = profile_name

    def get_aws_account_credentials(self, aws_account_number=None):
        """
        :param str aws_account_number:
            Optional for this provider, since the account number is implicit
            with whatever credentials we can find.
        """
        # TODO: check if the given aws_account_number (IFF one is given)
        # actually matches the account to which the found credentials belong.

        key_id = None
        secret_key = None
        session_token = None

        # First, check for AWS credential envrionment vars:
        #   - AWS_ACCESS_KEY_ID
        #   - AWS_SECRET_ACCESS_KEY
        #   - AWS_SESSION_TOKEN (optional)
        key_id = os.environ.get('AWS_ACCESS_KEY_ID')
        secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
        if key_id is not None and secret_key is not None:
            # We found creds in the environment.
            # Maybe there's a session token?
            session_token = os.environ.get('AWS_SESSION_TOKEN')

        else:
            # Next, check if a profile name was provided and get the creds from
            # that profile.
            # If no explicit profile name was given, try the "default" profile.
            profile_creds = self.boto3_session.get_credentials()
            if profile_creds is None:
                # If this is None, no matching profile for `self._profile_name`
                # was found in `~/.aws/credentials`, essentially.
                raise exceptions.CredentialsError(
                    'Unable to fetch AWS credentials: nothing found in the '
                    'environment or the AWS CLI profile "{profile}"'.format(
                        profile=self._profile_name
                    )
                )
            key_id = profile_creds.access_key
            secret_key = profile_creds.secret_key
            session_token = profile_creds.token

        # If no default profile exists even, raise an error.
        # TODO: test me
        return creds.AWSCredentials(
            aws_access_key_id=key_id,
            aws_secret_access_key=secret_key,
            aws_session_token=session_token,
        )

    @property
    def boto3_session(self):
        if self._session is None:
            self._session = boto3.session.Session(
                profile_name=self._profile_name
            )
        return self._session

    def aws_client(self, aws_account, aws_service, region_name=None, **kwargs):
        """
        :param aws_account:
            Ignored.
        """
        return self.boto3_session.client(
            aws_service, region_name=region_name, **kwargs
        )