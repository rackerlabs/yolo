# Changes

## 0.3.0 (3-Nov-2017)

- Add `--copy-from-stage` option to `yolo put-parameters`. Allows generic
  configuration parmaters to easily be copied across stages.
- Show some more helpful errors when a FAWS account is not found.
- Support basic AWS CLI credentials (not just FAWS).
- Report possible causes on a CloudFormation stack rollback to aid in
  debugging.
- Fix a bug was causing custom API Gateway authorizers to not get configured
  correctly.
- Make custom API Gateway authorizers optional.
- Add comprehensive documentation at https://yolocli.readthedocs.io.
- Add custom Dockerfile for building Python-based Lambda Functions.
- Remove [yoke](https://pypi.python.org/pypi/yoke) as a dependency.
- Let stages inherit `parameters` from the `default` stage. This removes a lot
  of potential duplication in `yolo.yaml` configuration.

## 0.2.0 (6-Oct-2017)

- Initial release to PyPI
