#!/bin/bash

set -ex

STG=dev
SVC=my-lambda-service

yolo deploy-infra --stage ${STG}
yolo build-lambda --stage ${STG} --service ${SVC}
yolo push --stage ${STG} --service ${SVC}

yolo put-parameters --stage ${STG} --service ${SVC} --use-defaults
yolo show-parameters --stage ${STG} --service ${SVC}
yolo ensure-parameters --stage ${STG} --service ${SVC}

VERSION=$(git rev-parse HEAD)
yolo deploy-lambda --stage ${STG} --service ${SVC} --version ${VERSION}

yolo show-service --stage ${STG} --service ${SVC}
