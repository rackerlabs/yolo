
 # flake8: noqa

PYTHON_DOCKERFILE = """\n
FROM amazonlinux
ARG python_version=python27
ARG deps

RUN echo $python_version

RUN yum -y update && \
    yum install -y ${python_version}-pip zip ${deps}

ENV python_version $python_version

CMD if [[ -n "$REBUILD_DEPENDENCIES" ]]; then \
        echo "rebuilding dependencies"; \
        mkdir /tmp/build; \
        /usr/bin/pip-${python_version:6:1}.${python_version:7:1} install \
            -r /dependencies/requirements.txt -t /tmp/build; \
        cd /tmp/build && \
            zip -r /build_cache/${DEPENDENCIES_SHA}.zip ./*; \
    else echo "using cached dependencies; no rebuild"; \
    fi && \
    cd /src && \
        rm -f /dist/lambda_function.zip && \
        cp /build_cache/${DEPENDENCIES_SHA}.zip /dist/lambda_function.zip && \
        zip -r /dist/lambda_function.zip ${INCLUDE} && \
    cd /tmp && \
        echo "{\"VERSION_HASH\": \"${VERSION_HASH}\", \"BUILD_TIME\": \"${BUILD_TIME}\"}" > config.json && \
        zip -r /dist/lambda_function.zip config.json
"""
