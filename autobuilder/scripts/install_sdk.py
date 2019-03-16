#!/usr/bin/env python
# ex:ts=4:sw=4:sts=4:et
# -*- tab-width: 4; c-basic-offset: 4; indent-tabs-mode: nil -*-
# Utility script for installing an SDK from tmp/deploy/sdk

import os
import sys
import re
import optparse
import time

from autobuilder.utils.logutils import Log
from autobuilder.utils import process

__version__ = "0.2.1"

log = Log(__name__)


def get_info(histdir, sdkname):
    infodict = {}
    pat = re.compile(r'^(.+?)\s*=\s*(.+)\n')
    infodir = os.path.join(histdir, sdkname)
    infofilename = os.path.join(infodir, 'sdk-info.txt')
    if not os.path.exists(infofilename):
        subdirs = os.listdir(infodir)
        if len(subdirs) != 1:
            raise RuntimeError("unable to identify SDK info for %s" % sdkname)
        infofilename = os.path.join(infodir, subdirs[0], 'sdk-info.txt')
    log.debug("SDK info file name for %s: %s", sdkname, infofilename)
    infofile = open(infofilename, 'r')
    for line in infofile:
        m = pat.match(line)
        if m is None:
            log.warn('Unparseable line in SDK %s info file:\n  %s',
                     sdkname, line)
            continue
        infodict[m.group(1)] = m.group(2)
        log.debug('%s = %s', m.group(1), m.group(2))
    infofile.close()
    return infodict


def find_default_install_dir(installerfile):
    pat = re.compile(r'^DEFAULT_INSTALL_DIR="(.+)"$')
    f = open(installerfile, 'r')
    for line in f:
        m = pat.match(line)
        if m is not None:
            f.close()
            return m.group(1)
    f.close()
    return None


class Sdk:
    def __init__(self, name, histdir):
        self.name = name
        self.infodict = get_info(histdir, name)

    def installerfile(self):
        return self.infodict['SDK_NAME'] + '-toolchain-' + self.infodict['SDK_VERSION'] + '.sh'

    def sdk_name(self):
        return self.infodict['SDK_NAME']

    def sdk_version(self):
        return self.infodict['SDK_VERSION']

    def distro(self):
        return self.infodict['DISTRO']

    def distro_version(self):
        return self.infodict['DISTRO_VERSION']

    def sdksize(self):
        return int(self.infodict['SDKSIZE'])

    def sdkmachine(self):
        return self.infodict['SDKMACHINE']


def main():
    global log
    parser = optparse.OptionParser(
        version="%prog version " + __version__,
        usage="""%prog [options] [sdk-name...]

Installs one or more SDKs created by a bitbake build, with
some validation checks to ensure that the new SDK does not
overwrite one that is already installed.

If you do not specify an SDK to install, all available SDKs
will be installed by default.  Use the --list option to list
the SDKs that the script can locate.

""")

    parser.add_option('-D', '--deploy-dir',
                      help='location of SDK deploy directory (default is tmp/deploy/sdk)',
                      action='store', dest='deploy_dir', default='tmp/deploy/sdk')
    parser.add_option('-H', '--history-dir',
                      help='location of buildhistory directory (default is buildhistory)',
                      action='store', dest='history_dir', default='buildhistory'),
    parser.add_option('-d', '--debug', help='increase the debug level',
                      action='count', dest='debug', default=0)
    parser.add_option('-v', '--verbose', help='verbose output',
                      action='store_true', dest='verbose')
    parser.add_option('-n', '--dry-run',
                      help='show, but do not execute, the generated commands',
                      action='store_true', dest='dry_run')
    parser.add_option('-i', '--install-root', help='Installation directory root',
                      action='store', dest='install_root')
    parser.add_option('-s', '--date-stamp', help='Date stamp to append to version string',
                      action='store', dest='date_stamp')
    parser.add_option('', '--no-stamp', help='Do not append date stamp to version string',
                      action='store_true', dest='nostamp')
    parser.add_option('-u', '--update-current', help='update the "current" symlink',
                      action='store_true', dest='update_current')
    parser.add_option('-l', '--list', help='List available SDKs instead of installing',
                      action='store_true', dest='do_list')
    parser.add_option('-m', '--machine', help='Target MACHINE name',
                      action='store', dest='machine')
    parser.add_option('', '--image', help='Image name', action='store', dest='image')

    options, args = parser.parse_args()

    log.set_level(options.debug, options.verbose)

    hdir = os.path.realpath(os.path.join(options.history_dir, 'sdk'))
    if not os.path.isdir(hdir):
        log.error("not a directory: %s (buildhistory)", hdir)
        return 1

    ddir = os.path.realpath(options.deploy_dir)
    if not os.path.isdir(ddir):
        log.error("not a directory: %s (deploy dir)", ddir)
        return 1

    sdkset = set(os.listdir(hdir))
    if len(sdkset) == 0:
        log.error("no SDKs found in %s", hdir)
        return 1

    # Make it easier on the autobuilder by allowing it to pass in
    # null arguments that we don't bother to interpret
    args = [arg for arg in args if arg != '']
    if len(args) > 0 and not options.do_list:
        argset = set(args)
        if not argset.issubset(sdkset):
            log.error("Unknown SDKs requested: [%s]",
                      ','.join(list(argset - sdkset)))
            return 1
        sdkset = argset

    sdklist = [Sdk(name, hdir) for name in sorted(list(sdkset))]

    if options.do_list:
        log.plain("Available SDKs:\n    %s\n",
                  '\n    '.join([sdk.name for sdk in sdklist]))
        return 0

    if not options.machine:
        log.error("missing --machine specifier")
        return 1

    if not options.image:
        log.error("missing --image specifier")
        return 1

    if options.image == 'buildtools-tarball':
        options.image = 'buildtools'
        options.machine = ''

    if not options.date_stamp:
        options.date_stamp = time.strftime("%Y%m%d")

    error_count = 0

    for sdk in sdklist:
        target = options.machine.replace('_', '-')
        installer = os.path.join(ddir, sdk.installerfile())
        if options.install_root:
            if options.nostamp:
                lastdir = sdk.sdk_version()
            else:
                lastdir = sdk.sdk_version().replace('-snapshot', '') + '-' + options.date_stamp
            destdir = os.path.join(options.install_root, target, lastdir)
            if os.path.exists(destdir):
                if options.nostamp:
                    log.error("destination directory %s exists - skipping", destdir)
                    error_count += 1
                    break
                for tagnum in range(99):
                    tag = "-%02d" % (tagnum + 1)
                    if not os.path.exists(destdir + tag):
                        destdir += tag
                        break
                else:
                    log.error("destination directory %s exists - skipping",
                              destdir)
                    error_count += 1
                    continue
                log.verbose("Destination: %s", destdir)
        else:
            destdir = None
            default_dest = find_default_install_dir(installer)
            log.debug('for %s, default installation directory: %s',
                      sdk.name, default_dest)
            if default_dest is not None and os.path.exists(default_dest):
                log.error("attempting to install over an existing SDK at %s - skipping",
                          default_dest)
                error_count += 1
                continue

        if options.dry_run:
            log.plain("bash %s%s -y", installer,
                      (" -d %s" % destdir) if destdir else "")
        else:
            # noinspection PyBroadException
            try:
                if destdir is not None:
                    cmd = "%s -d %s -y" % (installer, destdir)
                else:
                    cmd = "%s -y" % installer
                log.note("executing %s", cmd)
                (output, errors) = process.run(cmd)
                log.plain("%s", output)
            except Exception:
                # noinspection PyUnboundLocalVariable
                log.error("error installing %s:\n%s", sdk.name, errors)
                error_count += 1
                continue

        if destdir is not None:
            (parent, dest) = os.path.split(destdir)
            if options.update_current:
                cmd = "ln -snf %s %s" % (dest, os.path.join(parent, 'current'))
                if options.dry_run:
                    log.plain(cmd)
                    continue
                else:
                    # noinspection PyBroadException
                    try:
                        log.note("executing %s", cmd)
                        (output, errors) = process.run(cmd)
                        log.plain("%s", output)
                    except Exception:
                        log.warn("error updating current symlink:\n%s", errors)

    return error_count


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
