import io
import logging
import os
import tarfile
import time

LOG = logging.getLogger(__name__)

CONTAINER_POLL_INTERVAL = 10
FEEDBACK_IN_SECONDS = 60
STATUS_EXITED = 'exited'


def python_build_lambda_function(service_cfg):
    build_config = service_cfg['build']
    try:
        # Allow connecting to older Docker versions (e.g. CircleCI 1.0)
        client = docker.from_env(version='auto')
    except Exception:
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
        'INCLUDE': ' '.join(include),
        # TODO: deal wtih this
        # 'EXTRA_PACKAGES': '',
        'PY_VERSION': PYTHON_VERSION_MAP[runtime],
        'VERSION_HASH': utils.get_version_hash(),
        'BUILD_TIME': utils.now_timestamp(),
    }
    # TODO(larsbutler): make these file/dir names constants
    build_cache_dir = os.path.join(working_dir, '.yolo_build_cache')
    build_cache_version_file = os.path.join(
        build_cache_dir, 'cache_version.sha1'
    )

    LOG.info('Checking for dependencies cache...')
    # Decide if we need to rebuild dependencies based on cache contents:
    if os.path.isfile(build_cache_version_file):
        # Check the current cache version
        with open(build_cache_version_file) as fp:
            build_cache_version = fp.read().strip()
        LOG.info('Existing build cache version is %s', build_cache_version)

        if dependencies_sha1 != build_cache_version:
            # We must rebuild:
            LOG.info(
                'Build cache version mismatch. Rebuilding dependencies.'
            )
            environment['REBUILD_DEPENDENCIES'] = '1'
    else:
        # No cache found; we must build deps.
        LOG.info('No dependencies cache found.')
        environment['REBUILD_DEPENDENCIES'] = '1'

    container = client.containers.run(
        image=BUILD_IMAGE,
        # command='/bin/bash -c "./build_wheels.sh"',
        detach=True,
        environment=environment,
        volumes={
            working_dir: {'bind': '/src'},
            dependencies_path: {'bind': '/dependencies/requirements.txt'},
            dist_dir: {'bind': '/dist'},
            build_cache_dir: {'bind': '/build_cache'},
        },
    )
    LOG.info(
        "Build container started, waiting for completion (ID: %s)",
        container.short_id,
    )
    wait_for_container_to_finish(container)
    LOG.info("Build finished.")
    remove_container(container)


def wait_for_container_to_finish(container):
    """Wait for the container to finish and return the exit code (int)."""
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
    return exit_code


def remove_container(container, **kwargs):
    try:
        LOG.info('Removing build container...')
        container.remove(**kwargs)
        LOG.info('Removed build container')
    except Exception:
        # We just log an error and swallow the exception, because this happens
        # often on CircleCI.
        LOG.error(
            "Could not remove container, please remove it manually (ID: %s)",
            container.short_id,
        )


def put_files(container, src_dir, path, single_file_name=None):
    stream = io.BytesIO()

    with tarfile.open(fileobj=stream, mode='w') as tar:
        if single_file_name:
            arcname = single_file_name
        else:
            arcname = "/"
        tar.add(src_dir, arcname=arcname)
    stream.seek(0)
    container.put_archive(data=stream, path=path)


def create_build_volume_container(docker_client,
                                  image="alpine:3.6",
                                  working_dir=None,
                                  dependencies_path=None,
                                  dist_dir=None,
                                  build_cache_dir=None):
    docker_client.images.pull(image)
    working_dir_volume = docker_client.volumes.create()
    dependencies_volume = docker_client.volumes.create()
    dist_dir_volume = docker_client.volumes.create()
    build_cache_volume = docker_client.volumes.create()
    volume_container = docker_client.containers.create(
                 image, "/bin/true",
                 volumes=[
                           "{}:/src".format(working_dir_volume.name),
                           "{}:/dependencies".format(dependencies_volume.name),
                           "{}:/dist".format(dist_dir_volume.name),
                           "{}:/build_cache".format(build_cache_volume.name)
                         ])
    put_files(volume_container, working_dir, "/src")
    put_files(volume_container, dependencies_path, "/dependencies",
              single_file_name="requirements.txt")
    if os.path.isdir(build_cache_dir):
        # only copy build cache if it exists.
        put_files(volume_container, build_cache_dir, "/build_cache")
    return volume_container


def export_container_files(container, src_path, dst_path):
    # Copy build_cache from a container to a local directory.
    stream = io.BytesIO()
    tar_generator, _ = container.get_archive(src_path)

    for bytes in tar_generator:
        stream.write(bytes)
    else:
        stream.seek(0)

    with tarfile.open(fileobj=stream, mode='r') as tar:
        tar.extractall(path=dst_path)
