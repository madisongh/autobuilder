#!/usr/bin/env python
# Copyright 2014-2018 by Matthew Madison
# Distributed under license.

import os
import sys
import re
import stat
import optparse
import shutil
import autobuilder.utils.locks as locks
from datetime import date, timedelta
from autobuilder.utils.logutils import Log

__version__ = '0.2.4'

log = Log(__name__)


def do_cleanup(mirrorbase, subdir, options):
    """
    Walks the sstate-mirror tree, pruning any shared state
    files that are old enough (i.e., have a modification time
    older than prune_age days).

    Returns the number of files removed.
    """
    prune_age = timedelta(options.prune_age)
    now = date.today()
    removal_count = 0
    for dirpath, _, filenames in os.walk(os.path.join(mirrorbase, subdir)):
        for filename in filenames:
            if not (filename.endswith('.tgz') or filename.endswith('.siginfo')):
                log.debug(2, 'Not cleaning up %s', filename)
                continue
            mirrorfile = os.path.join(dirpath, filename)
            if os.path.islink(mirrorfile):
                log.warn('Found symlink in mirror: %s', mirrorfile)
                if not options.dry_run:
                    log.verbose('Removing symlink from mirror: %s', mirrorfile)
                    os.unlink(mirrorfile)
                continue
            statinfo = os.stat(mirrorfile)
            mtime = date.fromtimestamp(statinfo[stat.ST_MTIME])
            if now < mtime:
                log.warn('Modification time for %s (%s) ' +
                         'is later than today (%s)',
                         os.path.join(dirpath, filename), mtime.isoformat(),
                         now.isoformat())
                continue
            if now - mtime > prune_age:
                log.debug(1, '%s is old (mtime %s)', mirrorfile,
                          mtime.isoformat())
                removal_count += 1
                if options.dry_run:
                    log.plain('rm -f %s', mirrorfile)
                else:
                    log.verbose('Removing: %s', mirrorfile)
                    os.unlink(mirrorfile)
    return removal_count


def do_copy(cachebase, subdir, mirrorbase, options):
    """
    Walks the local sstate-cache tree, copying any shared-state packages
    created up to the corresponding location in the sstate-mirror tree,
    and optionally updating the modification time for any packages in the
    sstate-mirror that are symlinked in the local cache (so we know they
    were just used).  This touching is only needed for builds off older
    versions of OE-Core; more recent versions automatically do this as
    part of shared-state staging.

    Returns the number of files copied.
    """
    copy_count = 0
    for dirpath, _, filenames in os.walk(os.path.join(cachebase, subdir)):
        for filename in filenames:
            if not (filename.endswith('.tgz') or filename.endswith('.siginfo')):
                log.debug(2, 'Skipping copy of %s', filename)
                continue
            cachefile = os.path.join(dirpath, filename)
            if os.path.islink(cachefile):
                if not options.touch:
                    continue
                mirrorfile = os.path.realpath(os.readlink(cachefile))
                log.verbose('Updating modification time of %s', mirrorfile)
                if options.dry_run:
                    log.plain('touch %s', mirrorfile)
                else:
                    # noinspection PyBroadException
                    try:
                        os.utime(mirrorfile, None)
                    except Exception:
                        log.warn('Error occurred trying to update %s',
                                 mirrorfile)
                        pass
                continue
            relpath = os.path.relpath(cachefile, cachebase)
            mirrorfile = os.path.join(mirrorbase, relpath)
            mirrordir = os.path.dirname(mirrorfile)
            copy_count += 1
            if options.dry_run:
                log.plain('test -d %s || mkdir -p %s', mirrordir, mirrordir)
                log.plain('cp %s %s', cachefile, mirrordir)
            else:
                log.verbose('Copying %s to %s', cachefile, mirrordir)
                if not os.path.isdir(mirrordir):
                    os.makedirs(mirrordir)
                try:
                    shutil.copy(cachefile, mirrorfile)
                except IOError as err:
                    log.warn('Error occurred (errno=%d) copying %s to %s',
                             err.errno, cachefile, mirrorfile)
    return copy_count


def main():
    global log
    parser = optparse.OptionParser(
        version="%prog version " + __version__,
        usage="""%prog [options] dirname

Updates a shared-state mirror after a build.  Two modes of operation:
  Update mode:
    * copies sstate-* packages that were created during the build to
      the mirror location
    * touches sstate-* packages in the mirror location that were used
      during the build (to update the modification time)
  Clean mode:
    * removes sstate-* packages from the mirror directory that exceed
      a specified age and were not used in the build

Run this tool in update mode after each build, or each sub-build in a
set of related builds comprising a single build run.  Once a build run
has been completed, run this tool in clean mode to prune out old sstate
packages.
""")

    parser.add_option('-m', '--mode',
                      help='operation mode: update (default) or clean',
                      action='store', dest='mode', default='update',
                      type='choice', choices=['update', 'clean'])
    parser.add_option('-s', '--sstate-dir',
                      help='location of sstate-cache directory from build',
                      action='store', dest='sstate_dir', default='sstate-cache')
    parser.add_option('-d', '--debug', help='increase the debug level',
                      action='count', dest='debug', default=0)
    parser.add_option('-v', '--verbose', help='verbose output',
                      action='store_true', dest='verbose')
    parser.add_option('-t', '--touch', help='touch symlinked files',
                      action='store_true', dest='touch')
    parser.add_option('-n', '--dry-run',
                      help='display commands instead of executing them',
                      action='store_true', dest='dry_run')
    parser.add_option('-a', '--prune-age',
                      help='age, in days, to qualify files for removal',
                      action='store', dest='prune_age', type='int', default=30)
    options, args = parser.parse_args()
    if len(args) < 1:
        raise RuntimeError('no sstate-mirror directory name specified')
    if not os.path.isdir(args[0]):
        if not os.path.exists(args[0]):
            os.makedirs(args[0])
        else:
            raise RuntimeError('sstate-mirror directory %s not found' % args[0])
    log.set_level(options.debug, options.verbose)
    mirrorbase = os.path.realpath(args[0])
    lock = locks.lockfile(os.path.join(mirrorbase, '.updatelock'))
    if not lock:
        log.fatal('could not lock sstate-mirror directory')
        return 1
    if options.mode == 'clean':
        rmcount = 0
        for subdir in os.listdir(mirrorbase):
            rmcount += do_cleanup(mirrorbase, subdir, options)
        if options.dry_run:
            log.plain('# CLEAN: %d removals', rmcount)
        else:
            log.note('Removed %d stale entries', rmcount)
    elif options.mode == 'update':
        if not os.path.isdir(options.sstate_dir):
            log.note('sstate-cache directory %s not found - nothing to do',
                     options.sstate_dir)
            locks.unlockfile(lock)
            return 0
        lsbstr = None
        twohex = re.compile(r'^[0-9a-f][0-9a-f]$')
        lsbpat = re.compile(r'^(universal|[a-zA-z]+-[0-9]+\.[0-9]+)$')
        for subdir in os.listdir(options.sstate_dir):
            if lsbpat.match(subdir):
                log.debug(1, 'Found LSB subdirectory: %s', subdir)
                if lsbstr is not None:
                    log.warn('Multiple LSB subdirectories found')
                else:
                    lsbstr = subdir
                continue
            if not twohex.match(subdir):
                log.warn('Unrecognized directory found in sstate-cache: %s',
                         subdir)
        cachebase = os.path.realpath(options.sstate_dir)
        cpcount = 0
        for subdir in os.listdir(cachebase):
            if subdir != lsbstr and not twohex.match(subdir):
                log.debug(1, 'Skipping copy of %s',
                          os.path.join(cachebase, subdir))
            else:
                cpcount += do_copy(cachebase, subdir, mirrorbase, options)
        if options.dry_run:
            log.plain('# UPDATE: %d copies', cpcount)
        else:
            log.note('Copied %d new entries', cpcount)
        locks.unlockfile(lock)
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
