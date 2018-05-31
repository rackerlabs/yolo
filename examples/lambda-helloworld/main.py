import requests


def lambda_handler(event, context):
    return 'Hello world! requests version is {}'.format(requests.__version__)
