.. yolo documentation master file, created by
   sphinx-quickstart on Mon Oct  9 15:30:10 2017.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

Welcome to yolo's documentation!
================================

``yolo`` is a command line build/deployment tool for managing complete application stacks
on AWS infrastructure. ``yolo`` can deploy entire services (infrastructre and code)
with just a few commands. The following are supported:

- infrastructure deployments based on CloudFormation templates
- code deployments based on AWS Lambda Functions (plus API Gateway integration)
- code deployments based on static S3 website hosting (UI code, documentation, etc.)

``yolo`` also takes a modern approach to credentials and secrets management to keep
your deployment pipelines and application configurations secure.

``yolo`` is written in Python and can be installed from the
`Python Package Index <https://pypi.python.org/pypi>`_.

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   gettingstarted
   roadmap

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
