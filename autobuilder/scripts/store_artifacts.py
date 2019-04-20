#!/usr/bin/env python3
# Copyright 2019 by Matthew Madison
# Distributed under license.

import os
import sys
import re
import stat
import argparse
import urllib
import shutil
import tempfile
import autobuilder.utils.locks as locks
from datetime import date, timedelta
from autobuilder.utils.logutils import Log
from autobuilder.utils import s3session
from autobuilder.utils import process

__version__ = '0.1.0'

log = Log(__name__)


# noinspection PyBroadException
def copy_recursive(topdir, subdir, s3, destpath, filepat=None, tarball=False):
    """
    Walks a subdirectory under the build directory and copies all matching
    files under that subdirectory.  Symlinks are skipped, and if 'filepat'
    is specified, only filenames matching the specified regex pattern are
    copied.

    Returns the number of files copied.
    """
    filelist = None
    if tarball:
        filelist = tempfile.NamedTemporaryFile(mode='w', encoding='latin-1', delete=False)
    copy_count = 0
    if filepat:
        pat = re.compile(filepat)
    else:
        pat = None
    root = os.path.join(topdir, subdir)
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if pat and not pat.match(filename):
                continue
            localfile = os.path.join(dirpath, filename)
            if os.path.islink(localfile):
                continue
            relpath = localfile[len(root)+1:]

            copy_count += 1
            if tarball:
                filelist.write(relpath + '\n')
            elif s3:
                s3.upload(localfile, destpath + "/" + relpath)
                log.verbose('Uploaded %s -> %s' % (localfile, destpath + "/" + relpath))
            else:
                full_destpath = os.path.join(destpath, relpath)
                os.makedirs(os.path.dirname(full_destpath), exist_ok=True)
                try:
                    shutil.copy(localfile, full_destpath)
                    log.verbose('Copied %s -> %s' % (localfile, full_destpath))
                except IOError as err:
                    log.warn('Error occurred copying %s to %s: %s (%d)',
                             localfile, full_destpath, err.strerror, err.errno)
    if tarball:
        flname = filelist.name
        filelist.close()
        tarballname = os.path.join(workdir, os.path.basename(subdir) + '.tar.gz')
        try:
            cmd = ['tar', '-c', '-C', root, '--files-from', flname, '-z', '-f', tarballname]
            if log.verbosity or log.debug_level > 0:
                cmd.append('-v')
            output, errors = process.run(cmd)
            log.verbose(output)
            if s3:
                s3.upload(tarballname, destpath + "/" + os.path.basename(tarballname))
            else:
                full_destpath = os.path.join(destpath, os.path.basename(tarballname))
                os.makedirs(os.path.dirname(full_destpath), exist_ok=True)
                try:
                    shutil.copy(tarballname, full_destpath)
                    log.verbose('Copied %s -> %s' % (tarballname, full_destpath))
                except IOError as err:
                    log.warn('Error occurred copying %s to %s: %s (%d)',
                             tarballname, full_destpath, err.strerror, err.errno)
        except (process.CmdError, process.NotFoundError) as err:
            log.error("%s" % err)
        except process.ExecutionError as err:
            log.error("%s" % err.stderr)
        finally:
            # noinspection PyBroadException
            try:
                os.unlink(tarballname)
            except Exception:
                pass
        try:
            os.unlink(flname)
        except Exception:
            pass

    log.verbose('Copied %d file%s' % (copy_count, '' if copy_count == 1 else 's'))
    return copy_count


def main():
    global log
    global workdir
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--pull-request',
                        help='store artifacts of a PR build',
                        action='store_true', dest='pull_request')
    parser.add_argument('-s', '--storage-path',
                        help='URL or file path for storing artifacts',
                        action='store', dest='storage_path', required=True)
    parser.add_argument('-D', '--debug', help='increase the debug level',
                        action='count', dest='debug', default=0)
    parser.add_argument('-v', '--verbose', help='verbose output',
                        action='store_true', dest='verbose')
    parser.add_argument('-t', '--build-tag', help='build tag for this build',
                        action='store', dest='build_tag')
    parser.add_argument('-b', '--buildername', help='name of the builder',
                        action='store', dest='buildername')
    parser.add_argument('-i', '--imageset', help='name of the imageset built',
                        action='store', dest='imageset')
    parser.add_argument('-d', '--distro',
                        help='name of the distro being build',
                        action='store', dest='distro')
    parser.add_argument('-a', '--artifacts',
                        help='comma-separated list of artifacts to be stored',
                        action='store', dest='artifacts')
    parser.add_argument('builddir',
                        help='path to build directory ($BUILDDIR)',
                        action='store', default=os.getenv("BUILDDIR"))
    args = parser.parse_args()
    log.set_level(args.debug, args.verbose)
    workdir = args.builddir
    if not args.artifacts:
        log.plain('No artifacts requested, exiting')
        return 0
    spath = urllib.parse.urlparse(args.storage_path)
    if spath.scheme == 's3':
        s3 = s3session.S3Session(logger=log, bucket=spath.netloc)
        destpath = spath.path[1:]
    elif spath.scheme == 'file' or spath.scheme == '':
        s3 = None
        destpath = spath.path
        if not destpath.startswith('/'):
            destpath = '/' + destpath
    else:
        log.error('Unrecognized storage path: %s' % args.storage_path)
        return 1
    destpath += "/" + args.distro + "/" + args.buildername + "/" + args.build_tag
    for artifact in args.artifacts.lower().split(','):
        if artifact == 'images':
            copy_recursive(args.builddir, 'tmp/deploy/images', s3, destpath + "/images")
            continue
        if artifact == 'stamps':
            copy_recursive(args.builddir, 'tmp/stamps', s3, destpath,
                           filepat=r'.*sigdata.*', tarball=True)
            continue
        if artifact == 'buildhistory':
            copy_recursive(args.builddir, 'buildhistory', s3, destpath,
                           tarball=True)
            continue
        if artifact == 'sdk':
            copy_recursive(args.builddir, 'tmp/deploy/sdk', s3, destpath + "/sdk")
            continue
        log.warn('Unrecognized artifact requested: %s' % artifact)
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
