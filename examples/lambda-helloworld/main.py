import requests


def lambda_handler(event, context):
    response = """\
requests.__version__: {rv}
event: {event}
context: {context}""".format(
        rv=requests.__version__, event=event, context=vars(context)
    )
    return response
