# Copyright 2017 Rackspace US, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import difflib
import json
import os
import re
import subprocess
try:
    from StringIO import StringIO  # noqa
except ImportError:
    # Python3 fallback
    from io import BytesIO as StringIO  # noqa
import sys
import time


def get_version_hash():
    # Let's look for the easy way: CircleCI environment variable
    sha1 = os.environ.get('CIRCLE_SHA1', None)
    if sha1 is not None:
        return sha1

    try:
        sha1 = subprocess.check_output(
            'git log -1 | head -1 | cut -d" " -f2',
            shell=True,
        ).decode('utf-8').strip()
    except Exception as exc:
        print('Could not determine SHA1: {}'.format(exc))
        sha1 = None

    return sha1


def now_timestamp():
    """Get the current UTC time as a timestamp string.
    Example: '2017-05-11_19-44-47-110436'
    """
    return datetime.datetime.utcnow().strftime('%Y-%m-%d_%H-%M-%S-%f')


def get_unified_diff(a_data, b_data, fromfile=None, tofile=None):
    """Get a unified diff of two data structures (e.g., dicts or lists).

    :param a_data:
        String or any json serializable object. This represents the "from"
        version in the diff.
    :param b_data:
        String or any json serializable object. This represents the "to"
        version in the diff.
    :param str fromfile:
        Optional "file name" to include in the diff to represent the "from"
        version. It may be an actual file name, or it could simply be a
        resource name which is relevant to the diff.
    :param str tofile:
        Optional "file name" to include in the diff to represent the "to"
        version.
    """
    if isinstance(a_data, (list, tuple, dict)):
        a_output = json.dumps(a_data, sort_keys=True, indent=2).splitlines()
    else:
        a_output = a_data.splitlines()
    if isinstance(a_data, (list, tuple, dict)):
        b_output = json.dumps(b_data, sort_keys=True, indent=2).splitlines()
    else:
        b_output = b_data.splitlines()

    diff = '\n'.join(
        x.strip('\n')
        for x in difflib.unified_diff(
            a_output, b_output, fromfile=fromfile, tofile=tofile
        )
    )
    return diff


class DottedDict(dict):
    """Dictionary object allow keys to be accessed as attributes.

    For example, given the dictionary ``d = {'foo': {'bar': 123}}``, members
    can be accessed either with ``d['foo']['bar']`` or altnernatively with
    ``d.foo.bar``.

    Naturally, this limits key names to those which are valid attribute names.

    Credit to https://github.com/josh-paul for this implementation.
    """
    def __init__(self, *args, **kwargs):
        super(DottedDict, self).__init__(*args, **kwargs)
        for arg in args:
            if isinstance(arg, dict):
                for key, value in arg.items():
                    if isinstance(value, dict):
                        value = DottedDict(**value)
                    self[key] = value

        if kwargs:
            for key, value in kwargs.items():
                if isinstance(value, dict):
                    value = DottedDict(**value)
                self[key] = value

        # Catch for case of importing values in the .items() format
        if self.items() and not self.__dict__.items():
            for key, value in self.items():
                self.__setitem__(key, value)

    def __getattr__(self, attr):
        try:
            return self.__dict__[attr]
        # Do this to match python default behavior
        except KeyError:
            raise AttributeError(attr)

    def __setattr__(self, key, value):
        if self._is_valid_identifier(key):
            self.__setitem__(key, value)

    def __setitem__(self, key, value):
        if self._is_valid_identifier(key):
            super(DottedDict, self).__setitem__(key, value)
            self.__dict__.update({key: value})

    def __delattr__(self, item):
        self.__delitem__(item)

    def __delitem__(self, key):
        super(DottedDict, self).__delitem__(key)
        del self.__dict__[key]

    def _is_valid_identifier(self, identifier):
        """Test if a key identifier is valid according to the Python lexer.

        Source: https://stackoverflow.com/questions/10120295/valid-characters-in-a-python-class-name
        """
        python_keywords = [
            'False', 'None', 'True', 'and', 'as', 'assert', 'break', 'class',
            'continue', 'def', 'del', 'elif', 'else', 'except', 'finally',
            'for', 'from', 'global', 'if', 'import', 'in', 'is', 'lambda',
            'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while',
            'with', 'yield'
        ]
        if (identifier not in python_keywords and
                re.match('[a-zA-Z_][a-zA-Z0-9_]*', identifier)):
            return True
        raise SyntaxError(
            'Key name is not a valid identifier or is reserved keyword.'
        )


class S3UploadProgress(object):

    def __init__(self, filename):
        self._filename = filename
        self._size = float(os.path.getsize(filename))
        self._seen_so_far = 0
        self._start_time = time.time()

    @property
    def speed(self):
        elapsed = time.time() - self._start_time
        return self._seen_so_far / elapsed

    @property
    def eta(self):
        remaining_bytes = self._size - self._seen_so_far
        return remaining_bytes / self.speed

    @property
    def eta_string(self):
        return '{:.0f}m {:.0f}s'.format(self.eta // 60, self.eta % 60)

    def __call__(self, bytes_amount):
        self._seen_so_far += bytes_amount
        sys.stdout.write(
            "\rUploading {} - {:,} / {:,} kB ({:.2%}) - {:,} kB/s - ETA {}{}".format(
                os.path.basename(self._filename),
                int(self._seen_so_far / 1000.0),
                int(self._size / 1000),
                self._seen_so_far / self._size,
                int(self.speed / 1000.0),
                self.eta_string,
                '\n' if self._seen_so_far == self._size else '',
            )
        )
        sys.stdout.flush()
