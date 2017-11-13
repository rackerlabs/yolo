.. contents::

Getting Started
===============

Start here to get up and running with ``yolo``. The page contains information
on how to install ``yolo``, how to configure ``yolo`` for your projects, and
how to run ``yolo`` commands.

Installation
++++++++++++

The easiest way to get ``yolo`` is to install it from PyPI:

.. code-block:: bash

    $ pip install yolo

.. _yolo_yaml:

yolo.yaml configuration file
++++++++++++++++++++++++++++

``yolo`` :ref:`commands <commands_and_workflows>` are driven primarily by a YAML configuration file. The
following sections are required:

- :ref:`name <yolo_yaml_name>`
- :ref:`accounts <yolo_yaml_accounts>`
- :ref:`stages <yolo_yaml_stages>`
- :ref:`templates <yolo_yaml_templates>`
- :ref:`services <yolo_yaml_services>`

``yolo`` supports a limited set of :ref:`context variables <context_vars>` that can be used in configuration
values in the ``stages``, ``templates``, and ``services`` sections.

.. _yolo_yaml_name:

``name`` section
................
This section simply contains the name of your application. The name will be used
to label various pieces of infrastructure that ``yolo`` will create.

.. literalinclude:: gettingstarted.yolo.yaml
    :language: yaml
    :lines: 1
    :linenos:

.. _yolo_yaml_accounts:

``accounts`` section
....................
This section contains details about the AWS accounts where your infrastructure
is deployed. Define all of the relevant AWS account numbers and associated aliases
for all of your infrastructure accounts here.

You must also define a ``default_region``
for each account. The default region is used when deploying account level
infrastructure :ref:`templates <yolo_yaml_templates>`.

Depending on how you choose to separate the various stages/environments of your
application, it may be a good idea to make some deployments in separate accounts.

.. literalinclude:: gettingstarted.yolo.yaml
    :language: yaml
    :lines: 2-8
    :linenos:
    :lineno-start: 2


In the example above, we have separate accounts for development and production.

You must define at least one account.

.. _yolo_yaml_stages:

``stages`` section
..................
This section contains details about the various environments (or "stages") which
``yolo`` can deploy to. Define all of the relevant stages here, such as "dev", "staging",
"production", "QA", etc.

You may also define a special stage called ``default``. This stage definition can be used
to create custom stages on the fly without having to explicitly define them in the
configuration file. This is useful for creating stages for individual developers.

.. literalinclude:: gettingstarted.yolo.yaml
    :language: yaml
    :lines: 9-19
    :linenos:
    :lineno-start: 9

The name of a stage is used for many :ref:`commands <commands_and_workflows>` as the ``--stage`` parameter.

.. _yolo_yaml_templates:

``templates`` section
.....................
The templates section has two sub-sections: ``account`` and ``stage``:

.. literalinclude:: gettingstarted.yolo.yaml
    :language: yaml
    :lines: 20-30
    :linenos:
    :lineno-start: 20

- ``path`` indicates a directory path relative to the ``yolo.yaml`` file where
  CloudFormation templates are to be found. This directory location should contain
  a ``master.json`` or ``master.yaml`` template file.
- ``params`` are input parameters to the respective template. If your templates
  do not require any parameters, enter ``params: {}``.

If you don't use any CloudFormation templates to stand up your infrastucture,
you still have to define the ``templates`` section, but you can leave it empty:
``templates: {}``.

.. _yolo_yaml_services:

``services`` section
....................
There is where the bulk is ``yolo`` configuration is located. This section contains
details about how an application should be built, configured, and deployed.

.. literalinclude:: gettingstarted.yolo.yaml
    :language: yaml
    :lines: 31-
    :linenos:
    :lineno-start: 31

Under ``services``, we can have any number of services defined. Each service
must be defined with a unique name (e.g., **MyLambdaService**) which will be
used as the ``--service`` parameter in many ``yolo`` commands.

The details of service configuration can be quite complex. Let's break it down:

- ``type``: The type of service. Valid values are **s3**, **lambda**, and **lambda-apigateway**.
- ``bucket_name``: The target S3 bucket where this application is hosted. Only relevant for **s3**-type
  services.
- ``build``: Configuration used for build commands.
- ``build.working_dir``: Directory to be used as the root directory for builds.
  All other paths in ``build`` are assumed to be relative to ``build.working_dir``.
- ``build.dist_dir``: Location for ``yolo`` to place built artifacts after a successful
  ``yolo build-lambda`` command. ``yolo push`` will collect build artifacts from this location.
- ``build.dependencies``: File containing a list of build dependencies. For Python projects,
  this is the relative path to a **requirements.txt** file.
- ``build.include``: A list of relative files and directories to include in build artifacts.

- ``deploy``: Configuration used for service deployment commands.
- ``deploy.apigateway``: API Gateway-specific configuration. Only needed if the service ``type``
  is **lambda-apigateway**.
- ``deploy.apigateway.rest_api_name``: Unique name for the REST API. This will appear as the API
  name under the API Gateway dashboard in the AWS console.
  is **lambda-apigateway**.
- ``deploy.apigateway.swagger_template``: File path (relative to the ``yolo.yaml`` file) which
  contains the API defintion in `Swagger/OpenAPI <https://swagger.io/specification/>`_ format.
- ``deploy.apigateway.domains``: List of custom domains and base path mappings to wire up
  Lambda function handlers to the API. See
  `APIGateway.Client.create_base_path_mapping <https://boto3.readthedocs.io/en/latest/reference/services/apigateway.html#APIGateway.Client.create_base_path_mapping>`_.
  You must define at least one mapping.
- ``deploy.apigateway.authorizers``: Configuration for custom authorizers. This is useful
  if you need to integrate your service with an existing identity provider to provide
  authentication/authorization. See
  `APIGateway.Client.create_authorizer <https://boto3.readthedocs.io/en/latest/reference/services/apigateway.html#APIGateway.Client.create_authorizer>`_.
- ``deploy.apigateway.integration``: Configuration to wire up API Gateway requests to Lambda handlers.
  See `APIGateway.Client.put_integration <https://boto3.readthedocs.io/en/latest/reference/services/apigateway.html#APIGateway.Client.put_integration>`_.
- ``deploy.lambda_function_configuration``: Lambda Function runtime configuration. Only needed if the
  service ``type`` is **lambda** or **lambda-apigateway**.
  See `Lambda.Client.create_function <https://boto3.readthedocs.io/en/latest/reference/services/lambda.html#Lambda.Client.create_function>`_.
- ``deploy.parameters``: Runtime environment/configuration parameters. Parameters defined in this
  section are stored encrypted in
  `AWS's SSM Parameter Store <http://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-paramstore.html>`_
  Even configuration parameters which are not strictly secret are stored encrypted anyway.
- ``deploy.parameters.stages``: Sub-section for parameters for a specific stage.
- ``deploy.parameters.stages.default``: Special stage where common parameters can be defined
  in order to prevent duplication. Define parameters here which are common to all other stages.
- ``deploy.parameters.stages.<stage>``: List of parameters for a specific stage. Each parameter definition consists of an object with the following keys:
    - ``name``: [REQUIRED] Name of the parameter.
    - ``value``: [OPTIONAL] Default value for the parameter.
    - ``multiline``: [OPTIONAL] Boolean value indicating if this parameter consists of multiple lines.  This is useful for inputting multiline secrets such as private keys and SSL certificates.

.. note::

    ``deploy.apigateway.authorizers[].name`` needs to match what's specified in the ``securityDefinitions``
    section of the Swagger API specification.

.. _context_vars:

Context Variables
.................

There are handful of "context" variables which can be used to dynamically populate values in parts
of the ``yolo.yaml`` configuration. Context variables may only be used in the
:ref:`templates <yolo_yaml_templates>` and :ref:`services <yolo_yaml_services>` sections.

Context variables are rendered using `Jinja templating <http://jinja.pocoo.org/>`_ and can used
to replace a complete or partial string value. For example, ``'prefix-{{ stage.outputs.MyOutput }}-suffix'``.

The following contex variables are supported:

- ``account.name``: The alias of AWS account which is currently being used to run a command.
  Account names are defined in the :ref:`accounts <yolo_yaml_accounts>` section of the ``yolo.yaml`` file.
- ``account.account_number``: The account number of the current AWS account. Typically a 12-digit integer.
- ``account.outputs.<output-name>``: A specific output from the ``account`` CloudFormation stack.
  See :ref:`templates <yolo_yaml_templates>`. The ``<output-name>`` is the name of an item defined in the ``Outputs``
  section of the CloudFormation template. This is useful for dynamically referencing critical pieces of infrastructure--
  such as IAM roles or KMS keys--without having to hard-code the values.
- ``stage.name``: The name of the current stage, such as **dev**, **prod**, or **bob**.
- ``stage.region``: The AWS region where the current stage is hosted.
- ``stage.outputs.<output-name>``: Similar to ``account.outputs.<output-name>``, except that it is a reference
  to an output from the ``stage`` CloudFormation stack. See :ref:`templates <yolo_yaml_templates>`.

Often multiple context variables can be used together to assemble complex values. For example, an AWS ARN for
a given Lambda Function can be dynamically generated as follows:

.. code-block:: yaml

    arn:aws:lambda:{{ stage.region }}:{{ account.account_number }}:function:{{ stage.outputs.FunctionName }}

.. _project_structure:

Project structure
+++++++++++++++++

``yolo`` strives to not be too prescriptive about project structure and aims to
be flexible enough to support common patterns (and some uncommon ones) for structuring projects.

The following are some examples you can follow, whether you're starting a new project
or retrofitting an existing project with ``yolo``.

Example: Python service
.......................

.. code-block:: text
   :emphasize-lines: 1-2

    dist/
      lambda_function.zip
    lambda_main.py
    myapplication/
      api.py
      util.py
      db.py
    my-swagger-api.yaml
    requirements.txt
    setup.py
    yolo.yaml

Note the highlighted files above are created automatically by :ref:`yolo build-lambda <yolo_build_lambda>`.

``lambda_main.py``
``````````````````

AWS Lambda needs a "handler" or "entry point" for execution. It's a good practice to keep this file separate
from the rest of your codebase and as small as possible for a couple of reasons:

1. It prevents Lambda-specific patterns and constraints from propagating through your code base. This
   is important in the event that you're either

   a) porting an existing code base to run on Lambda; or
   b) porting a Lambda-ready code base to run on another platform (such as Docker/ECS)

2. It keeps things clean so that your application components are nicely decoupled and can be updated
   independently.

.. literalinclude:: gettingstarted_lambda_main.py
   :caption: Example:

``my-swagger-api.yaml``
```````````````````````

Applications based on Lambda and API Gateway require a detail API specification to be provided.
The simplest way to provide that definition is to use `Swagger <https://swagger.io>`_.

Below is an example Swagger API specification, written in YAML.

.. literalinclude:: gettingstarted_my-swagger-api.yaml

To build out your own API specification, here are some helpful links:

- `Swagger (OpenAPI) Specification <https://swagger.io/specification/>`_
- `Swagger Editor <https://editor.swagger.io/#/>`_

``requirements.txt``
````````````````````

This is the kind of basic `requirements file <https://pip.readthedocs.io/en/1.1/requirements.html>`_
that you will find in most Python projects. If your Lambda application requires any third-party
dependencies, specify those a ``requirements.txt`` file and :ref:`yolo build-lambda <yolo_build_lambda>` will
bundle them up for you.

This file needs to be referenced in the ``build`` section of your :ref:`service config <yolo_yaml_services>`.

``setup.py``
````````````

``setup.py`` is not strictly required for ``yolo`` to bundle and deploy your application,
however most Python projects will include one.

This file is only mentioned in this documentation to illustrate common patterns for laying
out Python projects.

``yolo.yaml``
`````````````

This file describes the general architecture of your service, as well as how to configure and deploy it.
Virtually everything you need (with this exception of infrastructure templates) to deploy your service
is contained in this file.

See :ref:`yolo.yaml <yolo_yaml>`.

Example: S3 service
...................

An S3 service is essentially just a collection of static assets which are stored in S3.
Examples of S3 services include:

- Statically hosted UI applications (see `<http://docs.aws.amazon.com/AmazonS3/latest/dev/WebsiteHosting.html>`_)
- Documentation
- Images, videos, or other large static assets

.. todo:: We need some simple, concrete examples of S3 services.

TODO: This section needs more detail.

.. _commands_and_workflows:

Commands and workflows
++++++++++++++++++++++

The following command sequences are list more or less order of relevance for standing up new projects.

Deploy account-level infrastructure
...................................

Generally the first thing you'll want to do is deploy any account-level infrastructure. This serves as
a baseline for all other infrastructure. Account-level infrastructure typically includes resources
such as:

- KMS encryption keys
- DNS hosted zones
- S3 buckets
- IAM roles

Account-level infrastructure should rarely change and should never be deleted (except in the case of a
complete tear-down).

.. code-block:: bash
   :caption: Example:

    $ yolo deploy-infra --account DevAccount

What's going on here?

- The command above will create or update a CloudFormation stack using the templates and parameters defined in the :ref:`templates.account <yolo_yaml_templates>`.
- ``DevAccount`` must be a valid account alias defined in the :ref:`accounts <yolo_yaml_accounts>` section.

You should repeat this process for each relevant account. This command can be used for new deployments or
to update existing deployments.

Deploy stage-level infrastructure
.................................

Similar to the command above, the following command will deploy stage-level infrastructure.

.. code-block:: bash

    $ yolo deploy-infra --stage dev

Stage-level infrastructure can change slightly more frequently that account-level infrastructure but
less frequently than application code. Stage-level infrastructure typically includes resources such
as:

- SNS topics
- SQS queues
- Various types of storage resources (such as S3 buckets, EBS volumes, or DynamoDB tables)
- Autoscaling groups
- EC2 instances (virtual machines)
- CloudFront CDN configurations
- Elastic Load Balancers
- DNS record sets
- IAM roles
- Security groups

These infrastructure stacks should be designed to completely stand-alone, with the exception of
some inputs from the account-level infrastructure.

Stage-level stacks should not be dependent on
one another, nor should they create any naming conflicts with a target AWS account. Dynamically
naming resources by using the ``stage.name`` :ref:`context variable <context_vars>` variable
as a :ref:`template parameter <yolo_yaml_templates>` is a good practice.

Show infrastructure stack status
................................

Show an overview of the deploy stacks (for all accounts and stages):

.. code-block:: bash

    $ yolo status

Store parameters in SSM
.......................

``yolo`` manages the storage and usage of configuration (especially secrets) through
`AWS SSM <http://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-paramstore.html>`_.

First, define the parameters your application needs in the
:ref:`services.\<service\>.parameters <yolo_yaml_services>` section. For parameters which are
not secret, you may define a default value. For parameters which are secret (such as passwords
and API keys), do not specify a default value.

Next, run the following command to upload parameters into a centralized location in SSM:

.. code-block:: bash

    $ yolo put-parameters --service MyLambdaService --stage dev --use-defaults
    Setting "db_password" to "password"
    Setting "log_level" to "info"
    Setting "db_username" to "admin"
    Enter "ssl_private_key" multiline value (ctrl+d when finished):-----BEGIN RSA PRIVATE KEY-----
    ryxjSsshEa9Ml08TA1YPjrEfQXRmdeVf9PJdSgV3zKI5+UV/g+J3MMxLZ/CRjhnn
    ...
    rjfwMC1qbyhXz/5so17CfdMCgYAv0ypMF4SU0ao73zObHFV08e7ced==
    -----END RSA PRIVATE KEY-----
    Environment configuration complete!

When you deploy an application, ``yolo`` will copy the centrally stored parameters to another
location in SSM in order to lock down the configuration for a given deployment. This makes
deployments less prone to mutation.

.. _yolo_build_lambda:

Build a Lambda application
..........................

To build a Lambda Function code package, run:

.. code-block:: bash

    $ yolo build-lambda --service MyLambdaService --stage dev

This will generate a code package (zipfile) which can be upload to AWS Lambda.

Create an application release
.............................

Once you've locally built a code package, create a release with it and ready it
for deployment:

.. code-block:: bash

    $ yolo push --service MyLambdaService --stage dev

This will place all the necessary build artifacts in an S3 and ready them
for deployment.

This commands works for **s3** services as well as **lambda** and **lambda-apigateway**
services.

.. note::

    This may seem like an extra and unncessary step, but if you need to rollback
    a deployment to fix a regression, it can be handy to reference a previous build
    in a central location (S3) without having to recreate the build from source.

List released application builds
................................

To list the builds which have been pushed for release, run:

.. code-block:: bash

    $ yolo list-builds --service MyLambdaService --stage dev
    Build                                     Timestamp
    ----------------------------------------  --------------------------
    b407b7ff4cdb61a4ffc7146b367a76a4a347e320  2017-10-09_12-51-54-143005
    876af6fe66c2e58f70b1c3b56dc88553c7bd4063  2017-10-09_11-57-37-302699
    370dfc794cf89ab07f93edcf2128b0327344b884  2017-10-09_11-33-19-071229
    18241c3aa4d7c223c2333a6cc8050c4c9fede333  2017-10-09_10-31-04-202881
    18241c3aa4d7c223c2333a6cc8050c4c9fede333  2017-10-09_10-25-00-666010

Take note of the ``Build`` version you want to deploy.

Deploy a Lambda service
.......................

To deploy a build of a Lambda-based service, run:

.. code-block:: bash

    $ yolo deploy-lambda --service MyLambdaService --stage dev --version 18241c3aa4d7c223c2333a6cc8050c4c9fede333

For **lambda** services, this command will create or update the target Lambda Function with
the release code and adjust specific configuration options (such as memory and timeout settings).
For **lambda-apigateway**  services, this command will create/update API Gateway integrations and API
definitions in addition to the Lambda Function updates.

.. note::

    Note that in the ``yolo list-builds`` example above there can be multiple builds
    for the same version. If you deploy from a version with multiple builds, ``yolo``
    will choose the most recent build according to its timestamp.

You also have the ability to deploy a new version of your Lambda Function from a local build, without
having to ``yolo push`` before deployment. It's useful for quick iterations.

.. code-block:: bash

    $ yolo deploy-lambda --service MyLambdaService --stage dev --from-local

Deploy an S3 service
....................

To deploy a build of an S3-based service, run:

.. code-block:: bash

    $ yolo deploy-s3 --service MyS3Service --stage dev --version 025c43fdfe41e54f5a8ab80723a7b1a9983df6c2

S3 service deployments are significantly simpler than those for Lambda-based services. Deployment simply
syncs artifacts from the build location (in S3) to the target S3 bucket indicated by the ``bucket_name``
defined in the :ref:`service configuration <yolo_yaml_services>`.

Overview of commands
....................

.. code-block:: bash

    $ yolo -h
    Usage: yolo [OPTIONS] COMMAND [ARGS]...

      Manage infrastructure and services on AWS for multiple accounts/stages.

      (Or, "yolo everything into prod".)

    Options:
      -h, --help  Show this message and exit.

    Commands:
      build-lambda           Build Lambda function packages.
      clear-config           Clear cached configuration for `yolo`.
      deploy-baseline-infra  DEPRECATED: Use `yolo deploy-infra` instead.
      deploy-infra           Deploy infrastructure from templates.
      deploy-lambda          Deploy Lambda functions for services.
      deploy-s3              Deploy a built S3 application.
      list-accounts          List AWS accounts.
      list-builds            List the pushed builds for a service/stage.
      list-lambda-builds     DEPRECATED: Use `yolo list-builds` instead.
      list-s3-builds         DEPRECATED: Use `yolo list-builds` instead.
      login                  Login with and cache Rackspace credentials.
      push                   Push a local build, ready it for deployment.
      push-lambda            DEPRECATED: Use `yolo push` instead.
      put-parameters         Securely store service/stage parameters.
      run                    Run a script with AWS account credentials.
      shell                  Launch a Python shell with AWS credentials.
      show-config            Show currently cached configuration.
      show-outputs           Show infrastructure stack outputs.
      show-parameters        Show centralized config for a service/stage.
      show-service           Show service configuration for a given stage.
      status                 Show infrastructure deployments status.
      upload-s3              DEPRECATED: Use `yolo push` instead.
