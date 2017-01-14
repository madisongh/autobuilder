# Copyright (c) 2014-2017 by Matthew Madison
# Distributed under license.

import re
import os
import time

from buildbot.process.factory import BuildFactory
from buildbot.process.properties import Interpolate, Property
from buildbot.process import properties
from buildbot.steps.shell import ShellCommand, SetPropertyFromCommand
from buildbot.steps.slave import RemoveDirectory
from buildbot.steps.source.git import Git
from buildbot.steps.transfer import StringDownload
from buildbot.steps.master import SetProperty
from buildbot.steps.trigger import Trigger
import buildbot.status.results as bbres
import abconfig
import distro as abdistro

ENV_VARS = {'PATH': Property('PATH'),
            'BB_ENV_EXTRAWHITE': Property('BB_ENV_EXTRAWHITE'),
            'BUILDDIR': Property('BUILDDIR')
            }


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
    return '%s-%04d' % (props.getProperty('datestamp'),
                        props.getProperty('main_buildnumber') or props.getProperty('buildnumber'))


# noinspection PyUnusedLocal
@properties.renderer
def build_datestamp(props):
    return time.strftime('%Y%m%d')


def build_output_path(props):
    return '%s/%s' % (props.getProperty('artifacts_path'),
                      build_tag(props))


@properties.renderer
def make_autoconf(props):
    result = ['INHERIT += "rm_work buildhistory"',
              props.getProperty('buildnum_template') % build_tag(props)]
    if abdistro.is_release_build(props):
        result.append('%s = ""' % props.getProperty('release_buildname_variable'))
    result.append('DL_DIR = "%s"' % props.getProperty('downloads_dir'))
    if props.getProperty('dl_mirrorvar') != "":
        result.append('%s = "%s"' % (props.getProperty('dl_mirrorvar'), props.getProperty('dl_mirror')))
        result.append('BB_GENERATE_MIRROR_TARBALLS = "1"\n')
    result.append(props.getProperty('sstate_mirrorvar') % props.getProperty('sstate_mirror'))
    result.append('BUILDHISTORY_DIR = "${TOPDIR}/buildhistory"')
    extraconfig = abconfig.buildslave_extraconfig(props)
    if len(extraconfig) > 0:
        result.append('\n' + extraconfig)
    # TODO: insert SRCREV_ settings for kernel
    return '\n'.join(result) + '\n'


@properties.renderer
def copy_artifacts_cmdseq(props):
    cmd = 'if [ -d tmp/deploy ]; then mkdir -p ' + build_output_path(props) + '; '
    cmd += 'for d in ' + props.getProperty('artifacts') + '; '
    cmd += 'do if [ -d tmp/deploy/$d ]; then cp -R tmp/deploy/$d '
    cmd += build_output_path(props) + '; fi; done; fi'
    return ['bash', '-c', cmd]


@properties.renderer
def save_stamps_cmdseq(props):
    stamps_dir = os.path.join(build_output_path(props), 'stamps')
    tarfile = props.getProperty('buildername') + '.tar.gz'
    cmd = 'if [ -d tmp/stamps ]; then mkdir -p ' + stamps_dir + '; '
    cmd += '(cd tmp/stamps; tar -c -z -f ' + os.path.join(stamps_dir, tarfile)
    cmd += ' . ); fi'
    return ['bash', '-c', cmd]


@properties.renderer
def save_history_cmdseq(props):
    history_dir = os.path.join(build_output_path(props), 'buildhistory')
    tarfile = props.getProperty('buildername') + '.tar.gz'
    cmd = 'if [ -d buildhistory ]; then mkdir -p ' + history_dir + '; '
    cmd += 'tar -c -z -f ' + os.path.join(history_dir, tarfile) + ' buildhistory; fi'
    return ['bash', '-c', cmd]


class DistroBuild(BuildFactory):
    def __init__(self, distro, repos):
        BuildFactory.__init__(self)
        self.addStep(SetProperty(property='datestamp',
                                 value=build_datestamp,
                                 name='SetDateStamp',
                                 description=['Setting', 'date', 'stamp'],
                                 descriptionDone=['Set', 'date', 'stamp']))
        repo = repos[distro.reponame]
        self.addStep(Git(repourl=repo.uri,
                         branch=distro.branch,
                         mode=('full' if repo.submodules else 'incremental'),
                         method='clobber',
                         codebase=distro.reponame,
                         workdir=os.path.join('sources', distro.name,
                                              distro.reponame)))
        if distro.kernelreponame:
            krepo = repos[distro.kernelreponame]
            kbranches = distro.kernelbranches
            for karch in kbranches:
                self.addStep(Git(repourl=krepo.uri,
                                 branch=kbranches[karch],
                                 mode='full', method='clobber',
                                 codebase=distro.kernelreponame,
                                 shallow=True,
                                 workdir=os.path.join('sources', distro.name,
                                                      distro.kernelreponame,
                                                      karch)))
            self.addStep(SetProperty(property='kernel_archs',
                                     value=' '.join(kbranches.keys()),
                                     name='SetKernelArchs'))
            self.addStep(SetProperty(property='kernel_srcrev',
                                     value=abconfig.kernel_srcrev,
                                     name='SetKernelSrcrev'))

        for otype in distro.host_oses:
            schedulers = [distro.name + '-' + imgset.name + '-' + otype
                          for imgset in distro.targets]
            trigger_props = {'datestamp': Property('datestamp'),
                             'main_buildnumber': Property('buildnumber'),
                             'buildtype': Property('buildtype'),
                             'kernel_srcrev': Property('kernel_srcrev'),
                             'primary_hostos': otype == distro.host_oses[0],
                             'save_artifacts': (otype == distro.host_oses[0])}
            self.addStep(Trigger(schedulerNames=schedulers,
                                 waitForFinish=(otype == distro.host_oses[0]),
                                 updateSourceStamp=True,
                                 set_properties=trigger_props))
        self.addStep(ShellCommand(command=['update-sstate-mirror',
                                           '--mode=clean', '-v',
                                           Property('sstate_mirror')],
                                  name='clean_sstate_mirror',
                                  timeout=None,
                                  description=['Cleaning', 'sstate', 'mirror'],
                                  descriptionDone=['Cleaned', 'sstate', 'mirror']))


class DistroImage(BuildFactory):
    def __init__(self, repourl, submodules=False, branch='master',
                 codebase='', imagedict=None, sdkmachines=None,
                 sdktargets=None):
        BuildFactory.__init__(self)
        self.addStep(Git(repourl=repourl, submodules=submodules,
                         branch=branch, codebase=codebase,
                         mode=('full' if submodules else 'incremental'),
                         method='clobber'))
        env_vars = ENV_VARS.copy()

        # Setup steps

        self.addStep(RemoveDirectory('build/build', name='cleanup',
                                     description=['Removing', 'old', 'build', 'directory'],
                                     descriptionDone=['Removed', 'old', 'build', 'directory']))
        self.addStep(SetPropertyFromCommand(command=['bash', '-c',
                                                     Interpolate('. %(prop:setup_script)s; printenv')],
                                            extract_fn=extract_env_vars,
                                            name='EnvironmentSetup',
                                            description=['Running', 'setup', 'script'],
                                            descriptionDone=['Ran', 'setup', 'script']))
        self.addStep(StringDownload(s=make_autoconf, slavedest='auto.conf',
                                    workdir='build/build/conf', name='make-auto.conf',
                                    description=['Creating', 'auto.conf'],
                                    descriptionDone=['Created', 'auto.conf']))

        # Build the target image(s)

        if imagedict is not None:
            for tgt in imagedict:
                tgtenv = env_vars.copy()
                tgtenv['MACHINE'] = tgt
                self.addStep(ShellCommand(command=['bash', '-c', 'bitbake %s' % imagedict[tgt]],
                                          env=tgtenv, workdir=Property('BUILDDIR'), timeout=None,
                                          name='%s_%s' % (imagedict[tgt], tgt),
                                          description=['Building', imagedict[tgt], '(' + tgt + ')'],
                                          descriptionDone=['Built', imagedict[tgt], '(' + tgt + ')']))
                self.addStep(ShellCommand(command=['move-images'], env=tgtenv,
                                          workdir=Property('BUILDDIR'), timeout=None,
                                          name='MoveImages-%s' % tgt,
                                          description=['Moving', 'images', 'for', tgt],
                                          descriptionDone=['Moved', 'images', 'for', tgt]))

        # Build the SDK(s)

        if sdktargets is not None:
            for tgt in sdktargets:
                tgtenv = env_vars.copy()
                tgtenv['MACHINE'] = tgt
                image = sdktargets[tgt]
                if image != 'buildtools-tarball':
                    # noinspection PyAugmentAssignment
                    image = '-c populate_sdk ' + image
                if sdkmachines is None:
                    self.addStep(ShellCommand(command=['bash', '-c', 'bitbake %s' % image],
                                              env=tgtenv, workdir=Property('BUILDDIR'), timeout=None,
                                              name='sdk-%s_%s' % (image, tgt),
                                              doStepIf=lambda step: abdistro.build_sdk(step.build.getProperties()),
                                              hideStepIf=lambda results, step: results == bbres.SKIPPED,
                                              description=['Building', 'SDK', image, '(' + tgt + ')'],
                                              descriptionDone=['Built', 'SDK', image, '(' + tgt + ')']))
                else:
                    for sdkmach in sdkmachines:
                        sdkenv = tgtenv.copy()
                        sdkenv['SDKMACHINE'] = sdkmach
                        self.addStep(ShellCommand(command=['bash', '-c', 'bitbake %s' % image],
                                                  env=sdkenv, workdir=Property('BUILDDIR'), timeout=None,
                                                  name='sdk-%s_%s_%s' % (sdkmach, image, tgt),
                                                  doStepIf=lambda step: abdistro.build_sdk(step.build.getProperties()),
                                                  hideStepIf=lambda results, step: results == bbres.SKIPPED,
                                                  description=['Building', sdkmach, 'SDK', image, '(' + tgt + ')'],
                                                  descriptionDone=['Built', sdkmach, 'SDK', image, '(' + tgt + ')']))

        self.addStep(ShellCommand(command=['autorev-report', 'buildhistory'],
                                  workdir=Property('BUILDDIR'),
                                  name='AutorevReport', timeout=None,
                                  description=['Generating', 'AUTOREV', 'report'],
                                  descriptionDone=['Generated', 'AUTOREV', 'report']))

        # Copy artifacts, stamps, buildhistory to binary repo

        self.addStep(ShellCommand(command=copy_artifacts_cmdseq, workdir=Property('BUILDDIR'),
                                  name='CopyArtifacts', timeout=None,
                                  doStepIf=lambda step: (step.build.getProperty('save_artifacts') and
                                                         step.build.getProperty('artifacts') != ''),
                                  hideStepIf=lambda results, step: results == bbres.SKIPPED,
                                  description=['Copying', 'artifacts', 'to', 'binary', 'repo'],
                                  descriptionDone=['Copied', 'artifacts', 'to', 'binary', 'repo']))
        self.addStep(ShellCommand(command=save_stamps_cmdseq, workdir=Property('BUILDDIR'),
                                  name='SaveStamps', timeout=None,
                                  description=['Saving', 'build', 'stamps'],
                                  descriptionDone=['Saved', 'build', 'stamps']))
        self.addStep(ShellCommand(command=save_history_cmdseq, workdir=Property('BUILDDIR'),
                                  name='SaveHistory', timeout=None,
                                  description=['Saving', 'buildhistory', 'data'],
                                  descriptionDone=['Saved', 'buildhistory', 'data']))
        self.addStep(ShellCommand(command=['update-sstate-mirror', '-v', '-s', 'sstate-cache',
                                           Property('sstate_mirror')], workdir=Property('BUILDDIR'),
                                  name='UpdateSharedState', timeout=None,
                                  description=['Updating', 'shared-state', 'mirror'],
                                  descriptionDone=['Updated', 'shared-state', 'mirror']))
        if sdktargets is not None:
            for tgt in sdktargets:
                cmd = ['install-sdk', abdistro.sdk_root, abdistro.sdk_stamp,
                       '--machine=%s' % tgt, '--image=%s' % sdktargets[tgt],
                       abdistro.sdk_use_current]
                self.addStep(ShellCommand(command=cmd, workdir=Property('BUILDDIR'),
                                          name='InstallSDKs', timeout=None,
                                          doStepIf=lambda step: abdistro.install_sdk(step.build.getProperties()),
                                          hideStepIf=lambda results, step: results == bbres.SKIPPED,
                                          description=['Installing', sdktargets[tgt], 'SDK', '(' + tgt + ')'],
                                          descriptionDone=['Installed', sdktargets[tgt], 'SDK', '(' + tgt + ')']))
