#!/usr/bin/env python
#
# Copyright (c) 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Runs Android's lint tool."""


import argparse
import os
import sys
import traceback
from xml.dom import minidom

from util import build_utils


_SRC_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                         '..', '..', '..'))


def _OnStaleMd5(changes, lint_path, config_path, processed_config_path,
                manifest_path, result_path, product_dir, sources, jar_path,
                cache_dir, resource_dir=None, can_fail_build=False,
                silent=False):

  def _RelativizePath(path):
    """Returns relative path to top-level src dir.

    Args:
      path: A path relative to cwd.
    """
    return os.path.relpath(os.path.abspath(path), _SRC_ROOT)

  def _ProcessConfigFile():
    if not config_path or not processed_config_path:
      return
    if not build_utils.IsTimeStale(processed_config_path, [config_path]):
      return

    with open(config_path, 'rb') as f:
      content = f.read().replace(
          'PRODUCT_DIR', _RelativizePath(product_dir))

    with open(processed_config_path, 'wb') as f:
      f.write(content)

  def _ProcessResultFile():
    with open(result_path, 'rb') as f:
      content = f.read().replace(
          _RelativizePath(product_dir), 'PRODUCT_DIR')

    with open(result_path, 'wb') as f:
      f.write(content)

  def _ParseAndShowResultFile():
    dom = minidom.parse(result_path)
    issues = dom.getElementsByTagName('issue')
    if not silent:
      print >> sys.stderr
      for issue in issues:
        issue_id = issue.attributes['id'].value
        message = issue.attributes['message'].value
        location_elem = issue.getElementsByTagName('location')[0]
        path = location_elem.attributes['file'].value
        line = location_elem.getAttribute('line')
        if line:
          error = '%s:%s %s: %s [warning]' % (path, line, message, issue_id)
        else:
          # Issues in class files don't have a line number.
          error = '%s %s: %s [warning]' % (path, message, issue_id)
        print >> sys.stderr, error.encode('utf-8')
        for attr in ['errorLine1', 'errorLine2']:
          error_line = issue.getAttribute(attr)
          if error_line:
            print >> sys.stderr, error_line.encode('utf-8')
    return len(issues)

  # Need to include all sources when a resource_dir is set so that resources are
  # not marked as unused.
  if not resource_dir and changes.AddedOrModifiedOnly():
    changed_paths = set(changes.IterChangedPaths())
    sources = [s for s in sources if s in changed_paths]

  with build_utils.TempDir() as temp_dir:
    _ProcessConfigFile()

    cmd = [
        _RelativizePath(lint_path), '-Werror', '--exitcode', '--showall',
        '--xml', _RelativizePath(result_path),
    ]
    if jar_path:
      cmd.extend(['--classpath', _RelativizePath(jar_path)])
    if processed_config_path:
      cmd.extend(['--config', _RelativizePath(processed_config_path)])
    if resource_dir:
      cmd.extend(['--resources', _RelativizePath(resource_dir)])

    # There may be multiple source files with the same basename (but in
    # different directories). It is difficult to determine what part of the path
    # corresponds to the java package, and so instead just link the source files
    # into temporary directories (creating a new one whenever there is a name
    # conflict).
    src_dirs = []
    def NewSourceDir():
      new_dir = os.path.join(temp_dir, str(len(src_dirs)))
      os.mkdir(new_dir)
      src_dirs.append(new_dir)
      cmd.extend(['--sources', _RelativizePath(new_dir)])
      return new_dir

    def PathInDir(d, src):
      return os.path.join(d, os.path.basename(src))

    for src in sources:
      src_dir = None
      for d in src_dirs:
        if not os.path.exists(PathInDir(d, src)):
          src_dir = d
          break
      if not src_dir:
        src_dir = NewSourceDir()
      os.symlink(os.path.abspath(src), PathInDir(src_dir, src))

    if manifest_path:
      cmd.append(_RelativizePath(os.path.join(manifest_path, os.pardir)))

    if os.path.exists(result_path):
      os.remove(result_path)

    env = {}
    stderr_filter = None
    if cache_dir:
      # When _JAVA_OPTIONS is set, java prints to stderr:
      # Picked up _JAVA_OPTIONS: ...
      env['_JAVA_OPTIONS'] = '-Duser.home=%s' % _RelativizePath(cache_dir)
      stderr_filter = lambda l: '' if '_JAVA_OPTIONS' in l else l

    try:
      build_utils.CheckOutput(cmd, cwd=_SRC_ROOT, env=env or None,
                              stderr_filter=stderr_filter)
    except build_utils.CalledProcessError:
      # There is a problem with lint usage
      if not os.path.exists(result_path):
        raise

      # Sometimes produces empty (almost) files:
      if os.path.getsize(result_path) < 10:
        if can_fail_build:
          raise
        elif not silent:
          traceback.print_exc()
        return

      # There are actual lint issues
      try:
        num_issues = _ParseAndShowResultFile()
      except Exception: # pylint: disable=broad-except
        if not silent:
          print 'Lint created unparseable xml file...'
          print 'File contents:'
          with open(result_path) as f:
            print f.read()
        if not can_fail_build:
          return

      if can_fail_build and not silent:
        traceback.print_exc()

      # There are actual lint issues
      try:
        num_issues = _ParseAndShowResultFile()
      except Exception: # pylint: disable=broad-except
        if not silent:
          print 'Lint created unparseable xml file...'
          print 'File contents:'
          with open(result_path) as f:
            print f.read()
        raise

      _ProcessResultFile()
      msg = ('\nLint found %d new issues.\n'
             ' - For full explanation refer to %s\n' %
             (num_issues,
              _RelativizePath(result_path)))
      if config_path:
        msg += (' - Wanna suppress these issues?\n'
                '    1. Read comment in %s\n'
                '    2. Run "python %s %s"\n' %
                (_RelativizePath(config_path),
                 _RelativizePath(os.path.join(_SRC_ROOT, 'build', 'android',
                                              'lint', 'suppress.py')),
                 _RelativizePath(result_path)))
      if not silent:
        print >> sys.stderr, msg
      if can_fail_build:
        raise Exception('Lint failed.')


def main():
  parser = argparse.ArgumentParser()
  build_utils.AddDepfileOption(parser)

  parser.add_argument('--lint-path', required=True,
                      help='Path to lint executable.')
  parser.add_argument('--product-dir', required=True,
                      help='Path to product dir.')
  parser.add_argument('--result-path', required=True,
                      help='Path to XML lint result file.')
  parser.add_argument('--cache-dir', required=True,
                      help='Path to the directory in which the android cache '
                           'directory tree should be stored.')
  parser.add_argument('--platform-xml-path', required=True,
                      help='Path to api-platforms.xml')
  parser.add_argument('--create-cache', action='store_true',
                      help='Mark the lint cache file as an output rather than '
                      'an input.')
  parser.add_argument('--can-fail-build', action='store_true',
                      help='If set, script will exit with nonzero exit status'
                           ' if lint errors are present')
  parser.add_argument('--config-path',
                      help='Path to lint suppressions file.')
  parser.add_argument('--enable', action='store_true',
                      help='Run lint instead of just touching stamp.')
  parser.add_argument('--jar-path',
                      help='Jar file containing class files.')
  parser.add_argument('--java-files',
                      help='Paths to java files.')
  parser.add_argument('--manifest-path',
                      help='Path to AndroidManifest.xml')
  parser.add_argument('--processed-config-path',
                      help='Path to processed lint suppressions file.')
  parser.add_argument('--resource-dir',
                      help='Path to resource dir.')
  parser.add_argument('--silent', action='store_true',
                      help='If set, script will not log anything.')
  parser.add_argument('--src-dirs',
                      help='Directories containing java files.')
  parser.add_argument('--stamp',
                      help='Path to touch on success.')

  args = parser.parse_args()

  if args.enable:
    sources = []
    if args.src_dirs:
      src_dirs = build_utils.ParseGypList(args.src_dirs)
      sources = build_utils.FindInDirectories(src_dirs, '*.java')
    elif args.java_files:
      sources = build_utils.ParseGypList(args.java_files)

    if args.config_path and not args.processed_config_path:
      parser.error('--config-path specified without --processed-config-path')
    elif args.processed_config_path and not args.config_path:
      parser.error('--processed-config-path specified without --config-path')

    input_paths = [
        args.lint_path,
        args.platform_xml_path,
    ]
    if args.config_path:
      input_paths.append(args.config_path)
    if args.jar_path:
      input_paths.append(args.jar_path)
    if args.manifest_path:
      input_paths.append(args.manifest_path)
    if args.resource_dir:
      input_paths.extend(build_utils.FindInDirectory(args.resource_dir, '*'))
    if sources:
      input_paths.extend(sources)

    input_strings = []
    if args.processed_config_path:
      input_strings.append(args.processed_config_path)

    output_paths = [ args.result_path ]

    build_utils.CallAndWriteDepfileIfStale(
        lambda changes: _OnStaleMd5(changes, args.lint_path,
                                    args.config_path,
                                    args.processed_config_path,
                                    args.manifest_path, args.result_path,
                                    args.product_dir, sources,
                                    args.jar_path,
                                    args.cache_dir,
                                    resource_dir=args.resource_dir,
                                    can_fail_build=args.can_fail_build,
                                    silent=args.silent),
        args,
        input_paths=input_paths,
        input_strings=input_strings,
        output_paths=output_paths,
        pass_changes=True)


if __name__ == '__main__':
  sys.exit(main())
