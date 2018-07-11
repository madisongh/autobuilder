#!/usr/bin/env python
# Copyright 2017-2018 by Matthew Madison
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

__version__ = '0.3.2'

log = Log(__name__)

IGNOREDIRS = ['bzr', 'cvs', 'git2', 'hg', 'svn']


def do_cleanup(mirrorbase, options):
    """
    Walks the downloads mirror tree, pruning any files
    that are old enough (i.e., have a modification time
    older than prune_age days).

    Returns the number of files removed.
    """
    whichtime = stat.ST_MTIME if options.touch else stat.ST_ATIME
    prune_age = timedelta(options.prune_age)
    now = date.today()
    removal_count = 0
    at_top = True
    for dirpath, dirnames, filenames in os.walk(mirrorbase, topdown=True):
        if at_top:
            at_top = False
            for d in IGNOREDIRS:
                if d in dirnames:
                    shutil.rmtree(os.path.join(dirpath, d), ignore_errors=True)
                    dirnames.remove(d)
        for filename in filenames:
            if filename == '.update-lock':
                continue
            mirrorfile = os.path.join(dirpath, filename)
            if os.path.islink(mirrorfile):
                log.warn('Found symlink in mirror: %s', mirrorfile)
                if not options.dry_run:
                    log.verbose('Removing symlink from mirror: %s', mirrorfile)
                    os.unlink(mirrorfile)
                continue
            statinfo = os.stat(mirrorfile)
            mtime = date.fromtimestamp(statinfo[whichtime])
            if now < mtime:
                log.warn('%s time for %s (%s) ' +
                         'is later than today (%s)',
                         "Modification" if options.touch else "Access",
                         os.path.join(dirpath, filename), mtime.isoformat(),
                         now.isoformat())
                continue
            if now - mtime > prune_age:
                log.debug(1, '%s is old (%stime %s)', mirrorfile,
                          'm' if options.touch else 'a', mtime.isoformat())
                removal_count += 1
                if options.dry_run:
                    log.plain('rm -f %s', mirrorfile)
                else:
                    log.verbose('Removing: %s', mirrorfile)
                    os.unlink(mirrorfile)
    return removal_count


def do_copy(cachebase, mirrorbase, options):
    """
    Walks the local downloads tree, copying any files created
    to the corresponding location in the downloads mirror tree,
    and updating the modification time for any files in the mirror
    that are symlinked in the local directory (so we know they were just used).

    Returns the number of files copied.
    """
    copy_count = 0
    at_top = True
    for dirpath, dirnames, filenames in os.walk(cachebase, topdown=True):
        if at_top:
            at_top = False
            for d in IGNOREDIRS:
                if d in dirnames:
                    dirnames.remove(d)
        for filename in filenames:
            if filename.endswith('.done'):
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

Updates a downloads mirror after a build.  Two modes of operation:
  Update mode:
    * copies downloaded packages to the mirror location
    * touches downloaded packages in the mirror location that were used
      during the build (optionally, to update the modification time)
  Clean mode:
    * removes downloaded packages from the mirror directory that exceed
      a specified age and were not used in the build.  Based on atime,
      unless --touch is specified, in which case it is based on mtime.

Run this tool in update mode after each build, or each sub-build in a
set of related builds comprising a single build run.  Once a build run
has been completed, run this tool in clean mode to prune out old downloads.

The '.done' marker files are not copied, nor are any source repositories
(git, svn, etc.).
""")

    parser.add_option('-m', '--mode',
                      help='operation mode: update (default) or clean',
                      action='store', dest='mode', default='update',
                      type='choice', choices=['update', 'clean'])
    parser.add_option('-l', '--location',
                      help='location of downloads directory from build',
                      action='store', dest='dl_dir', default='downloads')
    parser.add_option('-d', '--debug', help='increase the debug level',
                      action='count', dest='debug', default=0)
    parser.add_option('-v', '--verbose', help='verbose output',
                      action='store_true', dest='verbose')
    parser.add_option('-t', '--touch', help='touch symlinked files and use mtime for pruning checks',
                      action='store_true', dest='touch')
    parser.add_option('-n', '--dry-run',
                      help='display commands instead of executing them',
                      action='store_true', dest='dry_run')
    parser.add_option('-a', '--prune-age',
                      help='age, in days, to qualify files for removal',
                      action='store', dest='prune_age', type='int', default=180)
    options, args = parser.parse_args()
    if len(args) < 1:
        raise RuntimeError('no downloads mirror directory name specified')
    if not os.path.isdir(args[0]):
        if not os.path.exists(args[0]):
            os.makedirs(args[0])
        else:
            raise RuntimeError('downloads mirror directory %s not found' % args[0])
    log.set_level(options.debug, options.verbose)
    mirrorbase = os.path.realpath(args[0])
    lock = locks.lockfile(os.path.join(mirrorbase, '.update-lock'))
    if lock is None:
        log.fatal('could not lock downloads mirror for updating')
        return 1
    if options.mode == 'clean':
        rmcount = do_cleanup(mirrorbase, options)
        if options.dry_run:
            log.plain('# CLEAN: %d removals', rmcount)
        else:
            log.note('Removed %d stale entries', rmcount)
    elif options.mode == 'update':
        if not os.path.isdir(options.dl_dir):
            log.note('downloads directory %s not found - nothing to do',
                     options.dl_dir)
            locks.unlockfile(lock)
            return 0
        cachebase = os.path.realpath(options.dl_dir)
        cpcount = do_copy(cachebase, mirrorbase, options)
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
