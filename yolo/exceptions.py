class YoloError(Exception):
    pass


class NoInfrastructureError(YoloError):
    pass


class StackDoesNotExist(Exception):
    pass


class ResourceNotFound(Exception):
    pass
