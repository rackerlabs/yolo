from collections import namedtuple

AWSCredentials = namedtuple(
    'AWSCredentials',
    ['aws_access_key_id', 'aws_secret_access_key', 'aws_session_token'],
)
