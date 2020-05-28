#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""Module to conduct screen diff based notebook integration tests."""

# pytype: skip-file

from __future__ import absolute_import

import os
import platform
import threading
import unittest
from multiprocessing import Process

import pytest

from apache_beam.runners.interactive import interactive_environment as ie
from apache_beam.runners.interactive.testing.integration import notebook_executor

# TODO(BEAM-8288): clean up the work-around when Python2 support is deprecated.
try:
  from http.server import SimpleHTTPRequestHandler
  from http.server import HTTPServer
except ImportError:
  import SimpleHTTPServer as HTTPServer
  from SimpleHTTPServer import SimpleHTTPRequestHandler

try:
  import chromedriver_binary  # pylint: disable=unused-import
  from needle.cases import NeedleTestCase
  from needle.driver import NeedleChrome
  from selenium.webdriver.chrome.options import Options
  from selenium.webdriver.common.by import By
  from selenium.webdriver.support import expected_conditions
  from selenium.webdriver.support.ui import WebDriverWait
  _interactive_integration_ready = (
      notebook_executor._interactive_integration_ready)
except ImportError:
  _interactive_integration_ready = False

# Web elements will be rendered differently on different platforms. List all
# supported platforms with goldens here.
_SUPPORTED_PLATFORMS = ['Darwin', 'Linux']


class ScreenDiffIntegrationTestEnvironment(object):
  """A test environment to conduct screen diff integration tests for notebooks.
  """
  def __init__(self, test_notebook_path, golden_dir, cleanup=True):
    # type: (str, str, bool) -> None

    assert _interactive_integration_ready, (
        '[interactive_test] dependency is not installed.')
    assert os.path.exists(golden_dir), '{} does not exist.'.format(golden_dir)
    assert os.path.isdir(golden_dir), '{} is not a directory.'.format(
      golden_dir)
    self._golden_dir = golden_dir
    self._notebook_executor = notebook_executor.NotebookExecutor(
        test_notebook_path)
    self._cleanup = cleanup
    self._test_urls = {}
    self._server = None
    self._server_daemon = None

  def __enter__(self):
    self._notebook_executor.execute()
    with HTTPServer(('', 0), SimpleHTTPRequestHandler) as server:
      self._server = server

      def start_serving(server):
        server.serve_forever()

      self._server_daemon = Process(
          target=start_serving, args=[server], daemon=True)
      self._server_daemon.start()

      for test_id, output_path in\
        self._notebook_executor.output_html_paths.items():
        self._test_urls[test_id] = self.base_url + output_path

      return self

  def __exit__(self, exc_type, exc_value, traceback):
    if self._notebook_executor and self._cleanup:
      self._notebook_executor.cleanup()
    if self._server:

      def stop_serving(server):
        server.shutdown()

      threading.Thread(
          target=stop_serving, args=[self._server], daemon=True).start()
    if self._server_daemon:
      self._server_daemon.terminate()

  @property
  def base_url(self):
    """The base url where the locally started server serving HTMLs generated by
    notebook executions."""
    assert self._server, 'Server has not started.'
    host_n_port = self._server.server_address
    return 'http://{}:{}/'.format(host_n_port[0], host_n_port[1])

  @property
  def test_urls(self):
    """Mapping from test_id/execution_id to urls serving the output HTML pages
    generated by the corresponding notebook executions."""
    return self._test_urls

  @property
  def notebook_path_to_test_id(self):
    """Mapping from input notebook paths to their obfuscated execution/test ids.
    """
    return self._notebook_executor.notebook_path_to_execution_id


def should_skip():
  """Whether a screen diff test should be skipped."""
  return not (
      platform.system() in _SUPPORTED_PLATFORMS and
      ie.current_env().is_py_version_ready and
      ie.current_env().is_interactive_ready and _interactive_integration_ready)


if should_skip():

  @unittest.skip(
      reason='[interactive] and [interactive_test] deps are both required.')
  @pytest.mark.skip(
      reason='[interactive] and [interactive_test] deps are both required.')
  class BaseTestCase(unittest.TestCase):
    """A skipped base test case if interactive_test dependency is not installed.
    """
    pass

else:

  class BaseTestCase(NeedleTestCase):
    """A base test case to execute screen diff integration tests."""
    # Whether the browser should be headless.
    _headless = True

    def __init__(self, *args, **kwargs):
      """Initializes a test.

      Some kwargs that could be configured:

        #. golden_dir=<path>. A directory path pointing to all the golden
           screenshots as baselines for comparison.
        #. test_notebook_dir=<path>. A path pointing to a directory of
           notebook files in ipynb format.
        #. headless=<True/False>. Whether the browser should be headless when
           executing the tests.
        #. golden_size=<(int, int)>. The size of the screenshot to take and
           compare.
        #. cleanup=<True/False>. Whether to clean up the output directory.
           Should always be True in automated test environment. When debugging,
           turn it False to manually check the output for difference.
        #. threshold=<float>. An image difference threshold, when the image
           pixel distance is bigger than the value, the test will fail.
      """
      golden_root = kwargs.pop(
          'golden_dir',
          'apache_beam/runners/interactive/testing/integration/goldens')
      self._golden_dir = os.path.join(golden_root, platform.system())
      self._test_notebook_dir = kwargs.pop(
          'test_notebook_dir',
          'apache_beam/runners/interactive/testing/integration/test_notebooks')
      BaseTestCase._headless = kwargs.pop('headless', True)
      self._test_env = None
      self._viewport_width, self._viewport_height = kwargs.pop(
        'golden_size', (1024, 10000))
      self._cleanup = kwargs.pop('cleanup', True)
      self._threshold = kwargs.pop('threshold', 5000)
      self.baseline_directory = os.path.join(os.getcwd(), self._golden_dir)
      self.output_directory = os.path.join(
          os.getcwd(), self._test_notebook_dir, 'output')
      super(BaseTestCase, self).__init__(*args, **kwargs)

    @classmethod
    def get_web_driver(cls):
      chrome_options = Options()
      if cls._headless:
        chrome_options.add_argument('--headless')
      chrome_options.add_argument('--no-sandbox')
      chrome_options.add_argument('--disable-dev-shm-usage')
      chrome_options.add_argument('--force-color-profile=srgb')
      return NeedleChrome(options=chrome_options)

    def setUp(self):
      self.set_viewport_size(self._viewport_width, self._viewport_height)

    def run(self, result=None):
      with ScreenDiffIntegrationTestEnvironment(self._test_notebook_dir,
                                                self._golden_dir,
                                                self._cleanup) as test_env:
        self._test_env = test_env
        super(BaseTestCase, self).run(result)

    def explicit_wait(self):
      """Wait for common elements to be visible."""
      WebDriverWait(self.driver, 5).until(
          expected_conditions.visibility_of_element_located(
              (By.TAG_NAME, 'facets-overview')))
      WebDriverWait(self.driver, 5).until(
          expected_conditions.visibility_of_element_located(
              (By.TAG_NAME, 'facets-dive')))

    def assert_all(self):
      """Asserts screenshots for all notebooks in the test_notebook_path."""
      for test_id, test_url in self._test_env.test_urls.items():
        self.driver.get(test_url)
        self.explicit_wait()
        self.assertScreenshot('body', test_id, self._threshold)

    def assert_single(self, test_id):
      """Asserts the screenshot for a single test. The given test id will be the
      name of the golden screenshot."""
      test_url = self._test_env.test_urls.get(test_id, None)
      assert test_url, '{} is not a valid test id.'.format(test_id)
      self.driver.get(test_url)
      self.explicit_wait()
      self.assertScreenshot('body', test_id, self._threshold)

    def assert_notebook(self, notebook_name):
      """Asserts the screenshot for a single notebook. The notebook with the
      given notebook_name under test_notebook_dir will be executed and asserted.
      """
      if not notebook_name.endswith('.ipynb'):
        notebook_name += '.ipynb'
      notebook_path = os.path.join(self._test_notebook_dir, notebook_name)
      test_id = self._test_env.notebook_path_to_test_id.get(notebook_path, None)
      assert test_id, 'Cannot find notebook with name {}.'.format(notebook_name)
      self.assert_single(test_id)


# This file contains no tests. Below lines are purely for passing lint.
if __name__ == '__main__':
  unittest.main()