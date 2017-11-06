import boto3


class AWSCLICredentials(object):

    def __init__(self, profile_name):
        self.profile_name = profile_name
        self.session = boto3.session.Session(profile_name=profile_name)

    def get_aws_account_credentials(self, aws_account_number, duration=3600):
        # We'll just ignore all arguments, because credentials are already
        # available, we just need to get them from the session.
        creds = self.session.get_credentials()
        janus_style_creds = {
            'accessKeyId': creds.access_key,
            'secretAccessKey': creds.secret_key,
        }
        if creds.token:
            janus_style_creds['sessionToken'] = creds.token

        return {'credential': janus_style_creds}

    def boto3_session(self, acct_num):
        return self.session

    def aws_client(self, acct_num, aws_service, region_name=None, **kwargs):
        return self.session.client(aws_service, region_name=region_name, **kwargs)
