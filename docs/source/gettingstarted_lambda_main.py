from myapplication import api


def lambda_handler(event, context):
    """Main entry point of Lambda function.

    :param dict event:
        Dictionary containing the entire request template. This can vary wildly
        depending on the template structure and contents.
    :param context:
        Instance of an AWS Lambda Python Context object, as described on
        http://docs.aws.amazon.com/lambda/latest/dg/python-context-object.html.
    """
    return api.process_event(event, context)
