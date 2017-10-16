import hashlib
from io import BytesIO
import logging
import os
import time

import docker

from yolo import dockerfiles
from yolo import utils

LOG = logging.getLogger(__name__)

CONTAINER_POLL_INTERVAL = 10
FEEDBACK_IN_SECONDS = 60
STATUS_EXITED = 'exited'


def python_build_lambda_function(service_cfg):
    build_config = service_cfg['build']
    try:
        # Allow connecting to older Docker versions (e.g. CircleCI 1.0)
        client = docker.from_env(version='auto')
    except:
        LOG.error("Docker is not running, or it's outdated.")
        raise

    # TODO: check if the dir actually exists
    working_dir = os.path.abspath(build_config['working_dir'])

    dist_dir = os.path.abspath(build_config['dist_dir'])
    # List of files/dirs to include from `working_dir`
    include = build_config['include']
    runtime = service_cfg['deploy']['lambda_function_configuration']['Runtime']
    # TODO: check if the file actually exists
    dependencies_path = os.path.join(working_dir, build_config['dependencies'])
    with open(dependencies_path) as fp:
        dependencies_sha1 = hashlib.sha1(fp.read().encode('utf-8')).hexdigest()

    environment = {
        'DEPENDENCIES_SHA': dependencies_sha1,
        'INCLUDE': ' '.join(include),
        'VERSION_HASH': utils.get_version_hash(),
        'BUILD_TIME': utils.now_timestamp(),
    }
    # TODO(larsbutler): make these file/dir names constants
    build_cache_dir = os.path.join(working_dir, '.yolo_build_cache')
    build_cache_version_file = os.path.join(
        build_cache_dir, '{}.zip'.format(dependencies_sha1)
    )

    LOG.warning('Checking dependencies cache...')
    # Decide if we need to rebuild dependencies based on cache content:
    if os.path.isfile(build_cache_version_file):
        # Cached dependency archive was found, no need to rebuild.
        LOG.warning('Existing build cache version is %s', dependencies_sha1)
    else:
        # No cache found; we must build deps.
        LOG.warning('Build cache version mismatch or not found. '
                    'Rebuilding dependencies.')
        environment['REBUILD_DEPENDENCIES'] = '1'

    docker_file = BytesIO(dockerfiles.PYTHON_DOCKERFILE.encode('ascii'))
    image = client.images.build(
        fileobj=docker_file,
        tag='yolobuilder',
        buildargs={
            'python_version': runtime.replace('.', ''),
            # TODO: deal wtih this
            # 'extra_packages': '',
        },
    )

    container = client.containers.run(
        image=image.tags[0],
        detach=True,
        environment=environment,
        volumes={
            working_dir: {'bind': '/src'},
            dist_dir: {'bind': '/dist'},
            build_cache_dir: {'bind': '/build_cache'},
            dependencies_path: {'bind': '/dependencies/requirements.txt'},
        },
    )
    LOG.warning(
        "Build container started, waiting for completion (ID: %s)",
        container.short_id,
    )
    wait_for_container_to_finish(container)
    LOG.warning("Build finished.")
    remove_container(container)


def wait_for_container_to_finish(container):
    elapsed = 0
    while container.status != STATUS_EXITED:
        time.sleep(CONTAINER_POLL_INTERVAL)
        # Make sure we give some feedback to the user, that things are actually
        # happening in the background. Also, some CI systems detect the lack of
        # output as a build failure, which we'd like to avoid.
        elapsed += CONTAINER_POLL_INTERVAL
        if elapsed % FEEDBACK_IN_SECONDS == 0:
            LOG.warning("Container still running, please be patient...")

        container.reload()

    exit_code = container.attrs['State']['ExitCode']
    if exit_code != 0:
        # Save logs for further inspection -- if we are on CircleCI, save the
        # file under the artifacts directory.
        basepath = (
            os.environ['CIRCLE_ARTIFACTS']
            if 'CIRCLECI' in os.environ
            else '.'
        )
        log_filename = os.path.join(
            basepath,
            'container_{}.log'.format(container.short_id),
        )
        log_contents = container.logs(stdout=True, stderr=True)
        with open(log_filename, 'w') as fp:
            try:
                fp.write(log_contents)
            except TypeError:
                # On Python 3, `fp.write()` expects a string instead of bytes
                # (which is coming out of the `logs()` call), but Python 2
                # can't handle writing unicode to a file, so we can't do this
                # in both cases.
                fp.write(log_contents.decode('utf-8'))

        raise Exception(
            "Container exited with non-zero code. Logs saved to {}".format(
                log_filename)
        )


def remove_container(container):
    try:
        LOG.warning('Removing build container')
        container.remove()
    except:
        # We just log an error and swallow the exception, because this happens
        # often on CircleCI.
        LOG.error(
            "Could not remove container, please remove it manually (ID: %s)",
            container.short_id,
        )
