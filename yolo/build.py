import io
import logging
import os
import tarfile
import time

LOG = logging.getLogger(__name__)

CONTAINER_POLL_INTERVAL = 10
FEEDBACK_IN_SECONDS = 60
STATUS_EXITED = 'exited'


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
        LOG.warning('Removing build container')
        LOG.warning('kwargs is %s', kwargs)
        container.remove(**kwargs)
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
