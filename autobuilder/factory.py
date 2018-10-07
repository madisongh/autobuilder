# Copyright (c) 2014-2017 by Matthew Madison
# Distributed under license.

import re
import os
import time

from buildbot.plugins import steps, util
from buildbot.process.factory import BuildFactory
import buildbot.status.builder as bbres
from autobuilder import settings

ENV_VARS = {'PATH': util.Property('PATH'),
            'BB_ENV_EXTRAWHITE': util.Property('BB_ENV_EXTRAWHITE'),
            'BUILDDIR': util.Property('BUILDDIR')
            }


def _get_btinfo(props):
    abcfg = settings.get_config_for_builder(props.getProperty('autobuilder'))
    distro = abcfg.distrodict[props.getProperty('distro')]
    buildtype = props.getProperty('buildtype')
    return distro.btdict[buildtype]


def build_sdk(props):
    return _get_btinfo(props).build_sdk


def install_sdk(props):
    bt = _get_btinfo(props)
    return bt.install_sdk and not bt.pullrequesttype


def is_release_build(props):
    return _get_btinfo(props).production_release


def is_pull_request(props):
    return _get_btinfo(props).pullrequesttype


def without_sstate(props):
    return _get_btinfo(props).disable_sstate


@util.renderer
def sdk_root(props):
    root = _get_btinfo(props).sdk_root
    if root:
        return '--install-root=' + root
    else:
        return ''


@util.renderer
def sdk_use_current(props):
    return '--update-current' if _get_btinfo(props).current_symlink else ''


@util.renderer
def sdk_stamp(props):
    if _get_btinfo(props).production_release:
        return '--no-stamp'
    else:
        return '--date-stamp=' + (props.getProperty('datestamp') or time.strftime('%Y%m%d'))


@util.renderer
def dl_dir(props):
    dldir = props.getProperty('downloads_dir')
    if dldir:
        return dldir
    return 'downloads'


# noinspection PyUnusedLocal
def extract_env_vars(rc, stdout, stderr):
    pat = re.compile('^(' + '|'.join(ENV_VARS.keys()) + ')=(.*)')
    vardict = {}
    for line in stdout.split('\n'):
        m = pat.match(line)
        if m is not None:
            vardict[m.group(1)] = m.group(2)
    return vardict


def build_tag(props):
    if is_pull_request(props):
        return '%s-PR-%d' % (props.getProperty('datestamp') or time.strftime('%y%m%d'),
                             props.getProperty('prnumber'))
    return '%s-%04d' % (props.getProperty('datestamp') or time.strftime('%Y%m%d'),
                        props.getProperty('buildnumber'))


def build_output_path(props):
    return '%s/%s/%s' % (props.getProperty('artifacts_path'), props.getProperty('imageset'),
                         build_tag(props))


def worker_extraconfig(props):
    abcfg = settings.get_config_for_builder(props.getProperty('autobuilder'))
    wcfg = abcfg.worker_cfgs[props.getProperty('workername')]
    if wcfg:
        return wcfg.conftext
    return ''


@util.renderer
def make_autoconf(props):
    pr = is_pull_request(props)
    result = ['INHERIT += "rm_work%s"' % ('' if pr else ' buildhistory'),
              props.getProperty('buildnum_template') % build_tag(props)]
    if is_release_build(props):
        result.append('%s = ""' % props.getProperty('release_buildname_variable'))
    if props.getProperty('downloads_dir'):
        result.append('DL_DIR = "%s"' % props.getProperty('downloads_dir'))
    if props.getProperty('dl_mirrorvar') != "" and props.getProperty('dl_mirror') is not None:
        result.append(props.getProperty('dl_mirrorvar') % props.getProperty('dl_mirror'))
        if not pr:
            result.append('BB_GENERATE_MIRROR_TARBALLS = "1"')
    if props.getProperty('sstate_mirrorvar') != "":
        if without_sstate(props):
            result.append(props.getProperty('sstate_mirrorvar') % '/error/no/such/path')
        elif props.getProperty('sstate_mirror') is not None:
            result.append(props.getProperty('sstate_mirrorvar') % props.getProperty('sstate_mirror'))
    if not pr:
        result.append('BUILDHISTORY_DIR = "${TOPDIR}/buildhistory"')
    # Worker-specific config
    extraconfig = worker_extraconfig(props)
    if len(extraconfig) > 0:
        result.append(extraconfig)
    # Distro-specific config, can override worker config
    extraconfig = props.getProperty('extraconf')
    if len(extraconfig) > 0:
        result.append(extraconfig)
    # Buildtype-specific config, can override distro and worker configs
    extraconfig = _get_btinfo(props).extra_config
    if len(extraconfig) > 0:
        result.append(extraconfig)
    return '\n'.join(result) + '\n'


@util.renderer
def copy_artifacts_cmdseq(props):
    cmd = 'if [ -d tmp/deploy ]; then mkdir -p ' + build_output_path(props) + '; '
    cmd += 'for d in ' + props.getProperty('artifacts') + '; '
    cmd += 'do if [ -d tmp/deploy/$d ]; then cp -R tmp/deploy/$d '
    cmd += build_output_path(props) + '; fi; done; fi'
    return ['bash', '-c', cmd]


@util.renderer
def save_stamps_cmdseq(props):
    stamps_dir = os.path.join(build_output_path(props), 'stamps')
    tarfile = props.getProperty('buildername') + '.tar.gz'
    cmd = 'if [ -d tmp/stamps ]; then mkdir -p ' + stamps_dir + '; '
    cmd += '(cd tmp/stamps; tar -c -z -f ' + os.path.join(stamps_dir, tarfile)
    cmd += ' . ); fi'
    return ['bash', '-c', cmd]


@util.renderer
def save_history_cmdseq(props):
    history_dir = os.path.join(build_output_path(props), 'buildhistory')
    tarfile = props.getProperty('buildername') + '.tar.gz'
    cmd = 'if [ -d buildhistory ]; then mkdir -p ' + history_dir + '; '
    cmd += 'tar -c -z -f ' + os.path.join(history_dir, tarfile) + ' buildhistory; fi'
    return ['bash', '-c', cmd]


# noinspection PyUnusedLocal
@util.renderer
def datestamp(props):
    return str(time.strftime("%Y%m%d"))


class DistroImage(BuildFactory):
    def __init__(self, repourl, submodules=False, branch='master',
                 codebase='', imagedict=None, sdkmachines=None,
                 sdktargets=None):
        BuildFactory.__init__(self)
        self.addStep(steps.SetProperty(property='datestamp', value=datestamp))
        self.addStep(steps.Git(repourl=repourl, submodules=submodules,
                               branch=branch, codebase=codebase,
                               mode=('full' if submodules else 'incremental'),
                               method='clobber',
                               doStepIf=lambda step: not is_pull_request(step.build.getProperties()),
                               hideStepIf=lambda results, step: results == bbres.SKIPPED))
        self.addStep(steps.GitHub(repourl=repourl, submodules=submodules,
                                  branch=branch, codebase=codebase,
                                  mode=('full' if submodules else 'incremental'),
                                  method='clobber',
                                  doStepIf=lambda step: is_pull_request(step.build.getProperties()),
                                  hideStepIf=lambda results, step: results == bbres.SKIPPED))
        env_vars = ENV_VARS.copy()
        # First, remove duplicates from PATH,
        # then strip out the virtualenv bin directory if we're in a virtualenv.
        setup_cmd = 'PATH=`echo -n "$PATH" | awk -v RS=: -v ORS=: \'!arr[$0]++\'`;' + \
                    'if [ -n "$VIRTUAL_ENV" ]; then ' + \
                    'PATH=`echo "$PATH" | sed -re "s,(^|:)$VIRTUAL_ENV/bin(:|$),\\2,g;s,^:,,"`; ' + \
                    'fi; . %(prop:setup_script)s; printenv'
        # Setup steps

        self.addStep(steps.RemoveDirectory('build/build', name='cleanup',
                                           description=['Removing', 'old', 'build', 'directory'],
                                           descriptionDone=['Removed', 'old', 'build', 'directory']))
        self.addStep(steps.SetPropertyFromCommand(command=['bash', '-c',
                                                           util.Interpolate(setup_cmd)],
                                                  extract_fn=extract_env_vars,
                                                  name='EnvironmentSetup',
                                                  description=['Running', 'setup', 'script'],
                                                  descriptionDone=['Ran', 'setup', 'script']))
        self.addStep(steps.StringDownload(s=make_autoconf, workerdest='auto.conf',
                                          workdir='build/build/conf', name='make-auto.conf',
                                          description=['Creating', 'auto.conf'],
                                          descriptionDone=['Created', 'auto.conf']))

        # Build the target image(s)

        if imagedict is not None:
            for tgt in imagedict:
                tgtenv = env_vars.copy()
                tgtenv['MACHINE'] = tgt
                self.addStep(steps.ShellCommand(command=['bash', '-c', 'bitbake %s' % imagedict[tgt]],
                                                env=tgtenv, workdir=util.Property('BUILDDIR'), timeout=None,
                                                name='%s_%s' % (imagedict[tgt], tgt),
                                                description=['Building', imagedict[tgt], '(' + tgt + ')'],
                                                descriptionDone=['Built', imagedict[tgt], '(' + tgt + ')']))

        # Build the SDK(s)

        if sdktargets is not None:
            for tgt in sdktargets:
                tgtenv = env_vars.copy()
                tgtenv['MACHINE'] = tgt
                image = sdktargets[tgt]
                if image not in ['buildtools-tarball', 'uninative-tarball', 'meta-toolchain']:
                    # noinspection PyAugmentAssignment
                    image = '-c populate_sdk ' + image
                if sdkmachines is None:
                    self.addStep(steps.ShellCommand(command=['bash', '-c', 'bitbake %s' % image],
                                                    env=tgtenv, workdir=util.Property('BUILDDIR'), timeout=None,
                                                    name='sdk-%s_%s' % (image, tgt),
                                                    doStepIf=lambda step: build_sdk(step.build.getProperties()),
                                                    hideStepIf=lambda results, step: results == bbres.SKIPPED,
                                                    description=['Building', 'SDK', image, '(' + tgt + ')'],
                                                    descriptionDone=['Built', 'SDK', image, '(' + tgt + ')']))
                else:
                    for sdkmach in sdkmachines:
                        sdkenv = tgtenv.copy()
                        sdkenv['SDKMACHINE'] = sdkmach
                        self.addStep(steps.ShellCommand(command=['bash', '-c', 'bitbake %s' % image],
                                                        env=sdkenv, workdir=util.Property('BUILDDIR'), timeout=None,
                                                        name='sdk-%s_%s_%s' % (sdkmach, image, tgt),
                                                        doStepIf=lambda step: build_sdk(step.build.getProperties()),
                                                        hideStepIf=lambda results, step: results == bbres.SKIPPED,
                                                        description=['Building', sdkmach, 'SDK', image,
                                                                     '(' + tgt + ')'],
                                                        descriptionDone=['Built', sdkmach, 'SDK', image,
                                                                         '(' + tgt + ')']))

        self.addStep(steps.ShellCommand(command=['autorev-report', 'buildhistory'],
                                        workdir=util.Property('BUILDDIR'),
                                        name='AutorevReport', timeout=None,
                                        doStepIf=lambda step: not is_pull_request(step.build.getProperties()),
                                        hideStepIf=lambda results, step: results == bbres.SKIPPED,
                                        description=['Generating', 'AUTOREV', 'report'],
                                        descriptionDone=['Generated', 'AUTOREV', 'report']))

        # Copy artifacts, stamps, buildhistory to binary repo

        self.addStep(steps.ShellCommand(command=copy_artifacts_cmdseq, workdir=util.Property('BUILDDIR'),
                                        name='CopyArtifacts', timeout=None,
                                        doStepIf=lambda step: not is_pull_request(step.build.getProperties()) and
                                                              step.build.getProperty('artifacts') != '',
                                        hideStepIf=lambda results, step: results == bbres.SKIPPED,
                                        description=['Copying', 'artifacts', 'to', 'binary', 'repo'],
                                        descriptionDone=['Copied', 'artifacts', 'to', 'binary', 'repo']))
        self.addStep(steps.ShellCommand(command=save_stamps_cmdseq, workdir=util.Property('BUILDDIR'),
                                        name='SaveStamps', timeout=None,
                                        doStepIf=lambda step: not is_pull_request(step.build.getProperties()),
                                        hideStepIf=lambda results, step: results == bbres.SKIPPED,
                                        description=['Saving', 'build', 'stamps'],
                                        descriptionDone=['Saved', 'build', 'stamps']))
        self.addStep(steps.ShellCommand(command=save_history_cmdseq, workdir=util.Property('BUILDDIR'),
                                        name='SaveHistory', timeout=None,
                                        doStepIf=lambda step: not is_pull_request(step.build.getProperties()),
                                        hideStepIf=lambda results, step: results == bbres.SKIPPED,
                                        description=['Saving', 'buildhistory', 'data'],
                                        descriptionDone=['Saved', 'buildhistory', 'data']))
        self.addStep(steps.ShellCommand(command=['update-sstate-mirror', '-v', '-s', 'sstate-cache',
                                                 util.Property('sstate_mirror')], workdir=util.Property('BUILDDIR'),
                                        doStepIf=lambda step: not is_pull_request(step.build.getProperties()) and
                                                              step.build.getProperty('skip_sstate_update') != 'yes',
                                        hideStepIf=lambda results, step: results == bbres.SKIPPED,
                                        name='UpdateSharedState', timeout=None,
                                        description=['Updating', 'shared-state', 'mirror'],
                                        descriptionDone=['Updated', 'shared-state', 'mirror']))
        self.addStep(steps.ShellCommand(command=['update-downloads', '-v', '-l', dl_dir,
                                                 util.Property('dl_mirror')], workdir=util.Property('BUILDDIR'),
                                        doStepIf=lambda step: not is_pull_request(step.build.getProperties()) and
                                                              step.build.getProperty('dl_mirror') is not None,
                                        hideStepIf=lambda results, step: results == bbres.SKIPPED,
                                        name='UpdateDownloads', timeout=None,
                                        description=['Updating', 'downloads', 'mirror'],
                                        descriptionDone=['Updated', 'downloads', 'mirror']))
        if sdktargets is not None:
            for tgt in sdktargets:
                cmd = ['install-sdk', sdk_root, sdk_stamp,
                       '--machine=%s' % tgt, '--image=%s' % sdktargets[tgt],
                       sdk_use_current]
                self.addStep(steps.ShellCommand(command=cmd, workdir=util.Property('BUILDDIR'),
                                                name='InstallSDKs', timeout=None,
                                                doStepIf=lambda step: install_sdk(step.build.getProperties()),
                                                hideStepIf=lambda results, step: results == bbres.SKIPPED,
                                                description=['Installing', sdktargets[tgt], 'SDK', '(' + tgt + ')'],
                                                descriptionDone=['Installed', sdktargets[tgt], 'SDK', '(' + tgt + ')']))
