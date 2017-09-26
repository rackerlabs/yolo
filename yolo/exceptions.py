class YoloError(Exception):
    """Errors meant to be displayed to the user in a friendly way."""


class NoInfrastructureError(YoloError):
    pass


class StackDoesNotExist(Exception):
    pass


class ResourceNotFound(Exception):
    pass


class CloudFormationError(Exception):
    """Errors related to CloudFormation resource management."""
