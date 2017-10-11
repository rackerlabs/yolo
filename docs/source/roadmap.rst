Feature Roadmap
===============

The following features are planned for ``yolo``, more or less in order of importance:

- *Terraform support*: Currently only CloudFormation templates are supported. It would be
  useful for projects which already have infrastructure defined in Terraform templates
  to be able to take advantage of ``yolo`` with needing to port templates to CloudFormation.
- *ECS support*: Currently ``yolo`` only supports building/deploying backend applications based
  based on Lambda Functions running Python 2.7 or Python 3.6. Add ECS support for build/deploy
  commands would allow ``yolo`` to deploy virtually any application that can't or doesn't run
  in Lambda. This could be useful for retrofitting ``yolo`` deployment methods onto existing
  "legacy" projects.
- *Basic AWS credentials support*: Currently, ``yolo`` only supports fetching
  AWS credentials through Rackspace's Fanatical AWS API (which requires a
  Rackspace API key. This is a good feature, but it only helps for a small of use cases
  and many people could benefit from more general AWS support.
- *Additional runtime support for Lambda*: AWS Lambda support Python 2.7/3.6, Node.js 4.3.2/6.10.3,
  Java 8, and .NET Core 1.0.1 (C#). ``yolo`` currently only supports Python 2.7/3.6. It would be
  useful to expand support and enable more use cases.
