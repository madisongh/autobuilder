# Copyright (c) 2014-2017 by Matthew Madison
# Distributed under license.

import re
import time

import buildbot.status.builder as bbres
from buildbot.plugins import steps, util
from buildbot.process.factory import BuildFactory

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


def is_release_build(props):
    return _get_btinfo(props).production_release


def is_pull_request(props):
    return _get_btinfo(props).pullrequesttype


def without_sstate(props):
    return _get_btinfo(props).disable_sstate


def keep_going(props):
    return _get_btinfo(props).keep_going


def update_current_symlink(props):
    return _get_btinfo(props).current_symlink


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
            if m.group(1) == "BB_ENV_EXTRAWHITE":
                envvars = m.group(2).split()
                if "BBMULTICONFIG" not in envvars:
                    envvars.append("BBMULTICONFIG")
                vardict["BB_ENV_EXTRAWHITE"] = ' '.join(envvars)
            else:
                vardict[m.group(1)] = m.group(2)
    return vardict


def build_tag(props):
    if is_pull_request(props):
        return '%s-PR-%d' % (props.getProperty('datestamp') or time.strftime('%y%m%d'),
                             props.getProperty('prnumber'))
    return '%s-%04d' % (props.getProperty('datestamp') or time.strftime('%Y%m%d'),
                        props.getProperty('buildnumber'))


def worker_extraconfig(props):
    abcfg = settings.get_config_for_builder(props.getProperty('autobuilder'))
    wcfg = abcfg.worker_cfgs[props.getProperty('workername')]
    if wcfg:
        return wcfg.conftext or ''
    return ''


@util.renderer
def make_autoconf(props):
    pr = is_pull_request(props)
    result = ['INHERIT += "rm_work buildstats-summary%s"' % ('' if pr else ' buildhistory'),
              props.getProperty('buildnum_template') % build_tag(props)]
    if is_release_build(props):
        result.append('%s = ""' % props.getProperty('release_buildname_variable'))
    if props.getProperty('downloads_dir'):
        result.append('DL_DIR = "%s"' % props.getProperty('downloads_dir'))
    if props.getProperty('dl_mirrorvar') != "" and props.getProperty('dl_mirror') is not None:
        result.append(props.getProperty('dl_mirrorvar') % props.getProperty('dl_mirror'))
        if not pr:
            result.append('BB_GENERATE_MIRROR_TARBALLS = "1"')
            result.append('UPDATE_DOWNLOADS_MIRROR = "1"')
    if props.getProperty('sstate_mirrorvar') and props.getProperty('sstate_mirror'):
        result.append(props.getProperty('sstate_mirrorvar') % props.getProperty('sstate_mirror'))
        if not pr:
            result.append('UPDATE_SSTATE_MIRROR = "1"')
    if without_sstate(props):
        result.append('SSTATE_MIRRORS_forcevariable = ""')
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
def store_artifacts_cmd(props):
    cmd = ['store-artifacts', '--verbose']
    if is_pull_request(props):
        cmd.append('--pull-request')
    cmd.append('--storage-path=%s' % props.getProperty('artifacts_path'))
    cmd.append('--build-tag=%s' % build_tag(props))
    cmd.append('--buildername=' + props.getProperty('buildername'))
    cmd.append('--imageset=%s' % props.getProperty('imageset'))
    cmd.append('--distro=%s' % props.getProperty('distro'))
    cmd.append('--artifacts=%s' % props.getProperty('artifacts'))
    if update_current_symlink(props):
        cmd.append('--update-current')
    cmd.append(props.getProperty('BUILDDIR'))
    return cmd


@util.renderer
def bitbake_options(props):
    opts = ''
    if keep_going(props):
        opts += ' -k'


# noinspection PyUnusedLocal
@util.renderer
def datestamp(props):
    return str(time.strftime("%Y%m%d"))


class DistroImage(BuildFactory):
    def __init__(self, repourl, submodules=False, branch='master',
                 codebase='', imageset=None, triggers=None, extra_env=None):
        BuildFactory.__init__(self)
        self.addStep(steps.SetProperty(property='datestamp', value=datestamp))
        self.addStep(steps.Git(repourl=repourl, submodules=submodules,
                               branch=branch, codebase=codebase,
                               name='git-checkout-{}'.format(branch),
                               mode=('full' if submodules else 'incremental'),
                               method='clobber',
                               doStepIf=lambda step: not is_pull_request(step.build.getProperties()),
                               hideStepIf=lambda results, step: results == bbres.SKIPPED))
        if 'github.com' in repourl:
            self.addStep(steps.GitHub(repourl=repourl, submodules=submodules,
                                      branch=branch, codebase=codebase,
                                      name='git-checkout-pullrequest-ref',
                                      mode=('full' if submodules else 'incremental'),
                                      method='clobber',
                                      doStepIf=lambda step: is_pull_request(step.build.getProperties()),
                                      hideStepIf=lambda results, step: results == bbres.SKIPPED))
        env_vars = ENV_VARS.copy()
        if extra_env:
            env_vars.update(extra_env)
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
                                                  env=extra_env,
                                                  extract_fn=extract_env_vars,
                                                  name='EnvironmentSetup',
                                                  description=['Running', 'setup', 'script'],
                                                  descriptionDone=['Ran', 'setup', 'script']))
        self.addStep(steps.StringDownload(s=make_autoconf, workerdest='auto.conf',
                                          workdir=util.Interpolate("%(prop:BUILDDIR)s/conf"), name='make-auto.conf',
                                          description=['Creating', 'auto.conf'],
                                          descriptionDone=['Created', 'auto.conf']))

        if triggers:
            if isinstance(triggers, str):
                triggers = [triggers]
            self.addStep(steps.Trigger(schedulerNames=[alt + '-triggered' for alt in triggers],
                                       set_properties={'buildtype': util.Property('buildtype')}))

        if imageset.multiconfig:
            for img in imageset.imagespecs:
                mcconf = ['DEPLOY_DIR_MCSHARED = "${TOPDIR}/tmp/deploy"',
                          'DEPLOY_DIR_MCSHARED[vardepvalue] = "${DEPLOY_DIR}"',
                          'DEPLOY_DIR_IMAGE = "${DEPLOY_DIR_MCSHARED}/images/${MACHINE}"',
                          'DEPLOY_DIR_IMAGE[vardepvalue] = "${DEPLOY_DIR}/images/${MACHINE}"',
                          'SDK_DEPLOY_forcevariable = "${DEPLOY_DIR_MCSHARED}/sdk"',
                          'SDK_DEPLOY[vardepvalue] = "${DEPLOY_DIR}/sdk"',
                          'TMPDIR = "${TOPDIR}/tmp-%s"' % img.mcname]
                if img.machine:
                    mcconf.append('MACHINE="{}"'.format(img.machine))
                if img.sdkmachine:
                    mcconf.append('SDKMACHINE="{}"'.format(img.sdkmachine))
                self.addStep(steps.StringDownload(s='\n'.join(mcconf) + '\n', workerdest="%s.conf" % img.mcname,
                                                  workdir=util.Interpolate("%(prop:BUILDDIR)s/conf/multiconfig"),
                                                  name='make_mc_%s_%s' % (imageset.name, img.mcname),
                                                  description=['Creating', 'multiconfig', imageset.name, img.mcname],
                                                  descriptionDone=['Created', 'multiconfig',
                                                                   imageset.name, img.mcname]))
            target_images = [img for img in imageset.imagespecs if not img.is_sdk]
            sdk_images = [img for img in imageset.imagespecs if img.is_sdk]

            if target_images:
                tgtenv = env_vars.copy()
                tgtenv["BBMULTICONFIG"] = ' '.join([img.mcname for img in target_images])
                args = ["mc:{}:{}".format(img.mcname, arg) for img in target_images for arg in img.args]
                cmd = util.Interpolate("bitbake %(kw:bitbake_option)s " + ' '.join(args),
                                       bitbake_options=bitbake_options)
                self.addStep(steps.ShellCommand(command=['bash', '-c', cmd], timeout=None,
                                                env=tgtenv, workdir=util.Property('BUILDDIR'),
                                                name='build_%s_multiconfig' % imageset.name,
                                                description=['Building', imageset.name, '(multiconfig)'],
                                                descriptionDone=['Built', imageset.name, '(multiconfig)']))
            if sdk_images:
                tgtenv = env_vars.copy()
                tgtenv["BBMULTICONFIG"] = ' '.join([img.mcname for img in sdk_images])
                args = ["mc:{}:{}".format(img.mcname, arg) for img in sdk_images for arg in img.args]
                cmd = util.Interpolate("bitbake %(kw:bitbake_option)s -c populate_sdk " + ' '.join(args),
                                       bitbake_options=bitbake_options)
                self.addStep(steps.ShellCommand(command=['bash', '-c', cmd], timeout=None,
                                                env=tgtenv, workdir=util.Property('BUILDDIR'),
                                                name='build_sdk_%s_multiconfig' % imageset.name,
                                                description=['Building', 'SDK', imageset.name, '(multiconfig)'],
                                                descriptionDone=['Built', 'SDK', imageset.name, '(multiconfig)']))
        else:
            for i, img in enumerate(imageset.imagespecs, start=1):
                tgtenv = env_vars.copy()
                bbcmd = "bitbake"
                if img.is_sdk:
                    bbcmd += " -c populate_sdk"
                if img.machine:
                    tgtenv["MACHINE"] = img.machine
                if img.sdkmachine:
                    tgtenv["SDKMACHINE"] = img.sdkmachine
                cmd = util.Interpolate(bbcmd + " %(kw:bitbake_options)s " + ' '.join(img.args),
                                       bitbake_options=bitbake_options)
                self.addStep(steps.ShellCommand(command=['bash', '-c', cmd], timeout=None,
                                                env=tgtenv, workdir=util.Property('BUILDDIR'),
                                                name='build_%s_%s' % (imageset.name, img.name),
                                                description=['Building', imageset.name, img.name],
                                                descriptionDone=['Built', imageset.name, img.name]))

        self.addStep(steps.ShellCommand(command=store_artifacts_cmd, workdir=util.Property('BUILDDIR'),
                                        name='StoreArtifacts', timeout=None,
                                        description=['Storing', 'artifacts'],
                                        descriptionDone=['Stored', 'artifacts']))
