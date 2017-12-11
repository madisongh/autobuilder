#!/usr/bin/env python
# Copyright 2015 by Matthew Madison
# Distributed under license.

import os
import sys
import re
import optparse

from autobuilder.utils.logutils import Log

__version__ = '0.1'

log = Log(__name__)

AUTOREV_PAT = re.compile(r'^#\s*SRCREV\s*=\s*"\${AUTOREV}"')


def is_autorev(info_file):
    """
    Parses a 'latest_srcrev' file and returns true if it finds
    the comment indicating that AUTOREV was used.
    """
    retval = False
    f = open(info_file, 'r')
    for l in f:
        if AUTOREV_PAT.match(l) is not None:
            retval = True
            break
    f.close()
    return retval


def main():
    global log
    parser = optparse.OptionParser(
        version="%prog version " + __version__,
        usage="""%prog [options] buildhistory-dirname

Generates a report of packages for which AUTOREV was used for
the source revision during a build, by walking the buildhistory
directory tree and examining the latest_srcrev files.
""")

    parser.add_option('-d', '--debug', help='increase the debug level',
                      action='count', dest='debug', default=0)
    parser.add_option('-v', '--verbose', help='verbose output',
                      action='store_true', dest='verbose')
    options, args = parser.parse_args()
    if len(args) < 1:
        raise RuntimeError('no buildhistory directory name specified')
    if not os.path.isdir(args[0]):
            raise RuntimeError('buildhistory directory %s not found' % args[0])
    log.set_level(options.debug, options.verbose)
    buildhistbase = os.path.realpath(args[0])
    autorevcount = 0
    for dirpath, _, filenames in os.walk(os.path.join(buildhistbase, 'packages')):
        if 'latest_srcrev' in filenames:
            if is_autorev(os.path.join(dirpath, 'latest_srcrev')):
                log.note('recipe %s uses AUTOREV' % os.path.basename(dirpath))
                autorevcount += 1
    log.plain('%d recipe%s use AUTOREV' % (autorevcount, '' if autorevcount == 1 else 's'))
    return 0


if __name__ == "__main__":
    # noinspection PyBroadException
    try:
        ret = main()
        sys.exit(ret)
    except SystemExit:
        pass
    except Exception:
        import traceback

        traceback.print_exc(5)
        sys.exit(1)
