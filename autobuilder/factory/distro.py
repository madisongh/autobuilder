import time

from buildbot.plugins import util, steps
from buildbot.process.factory import BuildFactory
from buildbot.process.results import SKIPPED

import autobuilder.abconfig as abconfig
from autobuilder.factory.base import is_pull_request
from autobuilder.factory.base import extract_env_vars, dict_merge, ENV_VARS, datestamp


def build_tag(props):
    if is_pull_request(props):
        return '%s-PR-%d' % (props.getProperty('datestamp', default=time.strftime('%y%m%d')),
                             props.getProperty('prnumber'))
    return '%s-%04d' % (props.getProperty('datestamp', default=time.strftime('%Y%m%d')),
                        props.getProperty('buildnumber'))


@util.renderer
def make_autoconf(props):
    result = ['INHERIT += "rm_work buildstats-summary buildhistory"',
              'BUILDHISTORY_DIR = "${TOPDIR}/buildhistory"']
    # Worker-specific config
    result += props.getProperty('worker_extraconf', default=[])
    # Distro-specific config
    result += props.getProperty('extraconf', default=[])
    # Buildtype-specific config
    result += props.getProperty('buildtype_extraconf', default='').split('\n')

    return util.Interpolate('\n'.join(result) + '\n')


@util.renderer
def store_artifacts_cmd(props):
    cmd = ['store-artifacts', '--verbose']
    if is_pull_request(props):
        cmd.append('--pull-request')
    cmd.append('--storage-path=%s' % props.getProperty('artifacts_path'))
    cmd.append('--build-tag=%s' % build_tag(props))
    cmd.append('--buildername=' + props.getProperty('buildername'))
    cmd.append('--imageset=%s' % props.getProperty('imageset'))
    cmd.append('--distro=%s' % props.getProperty('DISTRO'))
    if not props.getProperty('noartifacts', default=False):
        cmd.append('--artifacts=%s' % ','.join(props.getProperty('artifacts')))
    if props.getProperty('current_symlink', default=False):
        cmd.append('--update-current')
    cmd.append(props.getProperty('BUILDDIR'))
    return cmd


@util.renderer
def bitbake_options(props):
    opts = ''
    if props.getProperty('keep_going', default=False):
        opts += ' -k'


class DistroImage(BuildFactory):
    def __init__(self, repourl, submodules=False, branch='master',
                 codebase='', imagesets=None, extra_env=None):
        BuildFactory.__init__(self)
        if extra_env is None:
            extra_env = {}
        self.addStep(steps.SetProperty(name='SetDatestamp',
                                       property='datestamp', value=datestamp))
        self.addStep(steps.Git(repourl=repourl, submodules=submodules,
                               branch=branch, codebase=codebase,
                               name='git-checkout-{}'.format(branch),
                               mode=('full' if submodules else 'incremental'),
                               method='clobber',
                               doStepIf=lambda step: not is_pull_request(step.build.getProperties()),
                               hideStepIf=lambda results, step: results == SKIPPED))
        self.addStep(steps.Git(repourl=repourl, submodules=submodules,
                               branch=branch, codebase=codebase,
                               name='git-checkout-pullrequest-ref',
                               mode=('full' if submodules else 'incremental'),
                               method='clobber',
                               doStepIf=lambda step: is_pull_request(step.build.getProperties()),
                               hideStepIf=lambda results, step: results == SKIPPED))
        # First, remove duplicates from original PATH (saved in ORIGPATH env var),
        # then strip out the virtualenv bin directory if we're in a virtualenv.
        setup_cmd = 'PATH=`echo -n "$ORIGPATH" | awk -v RS=: -v ORS=: \'!arr[$0]++\'`;' + \
                    'if [ -n "$VIRTUAL_ENV" ]; then ' + \
                    'PATH=`echo "$PATH" | sed -re "s,(^|:)$VIRTUAL_ENV/bin(:|$),\\2,g;s,^:,,"`; ' + \
                    'fi;%(prop:clean_env_cmd)s. %(prop:setup_script)s; bitbake -e | grep "^DISTROOVERRIDES="; printenv'
        # Setup steps

        # Clean copy of original PATH, before any setup scripts have been run, to ensure
        # we start fresh before each imageset, when we're running them sequentially.
        env_init_cmd = 'export ORIGPATH="$PATH"; %(prop:clean_env_cmd)sprintenv'
        self.addStep(steps.SetPropertyFromCommand(command=['bash', '-c', util.Interpolate(env_init_cmd)],
                                                  env=extra_env,
                                                  extract_fn=extract_env_vars,
                                                  name='save_path',
                                                  description="Saving",
                                                  descriptionSuffix=["original", "PATH"],
                                                  descriptionDone="Saved"))
        for imageset in imagesets:
            self.addStep(steps.SetProperty(name='SetImageSet_{}'.format(imageset.name),
                                           property='imageset', value=imageset.name))
            imageset_env = {"ORIGPATH": util.Property("ORIGPATH")}
            if imageset.distro is not None:
                imageset_env['DISTRO'] = imageset.distro
                self.addStep(steps.SetProperty(name='SetImagesetDistro_{}'.format(imageset.name),
                                               property='DISTRO', value=imageset.distro))

            if imageset.artifacts is not None:
                self.addStep(steps.SetProperty(name='SetImagesetArtifacts={}'.format(imageset.name),
                                               property='artifacts', value=','.join(imageset.artifacts)))

            self.addStep(steps.RemoveDirectory('build/build', name='cleanup_{}'.format(imageset.name),
                                               description="Removing old build directory",
                                               descriptionDone="Removed old build directory"))

            self.addStep(steps.SetPropertyFromCommand(command=['bash', '-c',
                                                               util.Interpolate(setup_cmd)],
                                                      env=dict_merge(extra_env, imageset_env),
                                                      extract_fn=extract_env_vars,
                                                      name='EnvironmentSetup_{}'.format(imageset.name),
                                                      description="Running",
                                                      descriptionSuffix=["setup", "script"],
                                                      descriptionDone="Ran"))
            self.addStep(steps.StringDownload(s=make_autoconf, workerdest='auto.conf',
                                              workdir=util.Interpolate("%(prop:BUILDDIR)s/conf"),
                                              name='make-auto.conf-{}'.format(imageset.name),
                                              description="Creating",
                                              descriptionSuffix=["auto.conf"],
                                              descriptionDone="Created"))

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
                                                      description="Creating",
                                                      descriptionSuffix=["multiconfig", imageset.name, img.mcname],
                                                      descriptionDone="Created"))
                target_images = [img for img in imageset.imagespecs if not img.is_sdk]
                sdk_images = [img for img in imageset.imagespecs if img.is_sdk]

                tgtenv = dict_merge(ENV_VARS, extra_env)
                tgtenv["BBMULTICONFIG"] = ' '.join([img.mcname for img in target_images])
                cmd = util.Interpolate("%(prop:clean_env_cmd)sbitbake %(kw:bitbake_option)s pseudo-native",
                                       bitbake_options=bitbake_options)
                self.addStep(steps.ShellCommand(command=['bash', '-c', cmd], timeout=None,
                                                env=tgtenv, workdir=util.Property('BUILDDIR'),
                                                name='build_pseudo_native',
                                                description="Building",
                                                descriptionSuffix=["pseudo-native"],
                                                descriptionDone="Built"))
                if target_images:
                    tgtenv = dict_merge(ENV_VARS, extra_env)
                    tgtenv["BBMULTICONFIG"] = ' '.join([img.mcname for img in target_images])
                    args = ["mc:{}:{}".format(img.mcname, arg) for img in target_images for arg in img.args]
                    cmd = util.Interpolate("%(prop:clean_env_cmd)sbitbake %(kw:bitbake_option)s " + ' '.join(args),
                                           bitbake_options=bitbake_options)
                    self.addStep(steps.ShellCommand(command=['bash', '-c', cmd], timeout=None,
                                                    env=tgtenv, workdir=util.Property('BUILDDIR'),
                                                    name='build_%s_multiconfig' % imageset.name,
                                                    description="Building",
                                                    descriptionSuffix=[imageset.name, "(multiconfig)"],
                                                    descriptionDone="Built"))
                if sdk_images:
                    tgtenv = dict_merge(ENV_VARS, extra_env)
                    tgtenv["BBMULTICONFIG"] = ' '.join([img.mcname for img in sdk_images])
                    args = ["mc:{}:{}".format(img.mcname, arg) for img in sdk_images for arg in img.args]
                    cmd = util.Interpolate("%(prop:clean_env_cmd)sbitbake %(kw:bitbake_option)s -c populate_sdk " + ' '.join(args),
                                           bitbake_options=bitbake_options)
                    self.addStep(steps.ShellCommand(command=['bash', '-c', cmd], timeout=None,
                                                    env=tgtenv, workdir=util.Property('BUILDDIR'),
                                                    name='build_sdk_%s_multiconfig' % imageset.name,
                                                    description="Building",
                                                    descriptionSuffix=["SDK", imageset.name, "(multiconfig)"],
                                                    descriptionDone="Built"))
            else:
                for i, img in enumerate(imageset.imagespecs, start=1):
                    tgtenv = dict_merge(ENV_VARS, extra_env)
                    bbcmd = "bitbake"
                    if img.is_sdk:
                        bbcmd += " -c populate_sdk"
                    if img.machine:
                        tgtenv["MACHINE"] = img.machine
                    if img.sdkmachine:
                        tgtenv["SDKMACHINE"] = img.sdkmachine
                    if i == 1:
                        cmd = util.Interpolate("%(prop:clean_env_cmd)sbitbake %(kw:bitbake_option)s pseudo-native",
                                               bitbake_options=bitbake_options)
                        self.addStep(steps.ShellCommand(command=['bash', '-c', cmd], timeout=None,
                                                        env=tgtenv, workdir=util.Property('BUILDDIR'),
                                                        name='build_pseudo_native',
                                                        description="Building",
                                                        descriptionSuffix=["pseudo-native"],
                                                        descriptionDone="Built"))
                    cmd = util.Interpolate("%(prop:clean_env_cmd)s" + bbcmd + " %(kw:bitbake_options)s " +
                                           ' '.join(img.args),
                                           bitbake_options=bitbake_options)
                    self.addStep(steps.ShellCommand(command=['bash', '-c', cmd], timeout=None,
                                                    env=tgtenv, workdir=util.Property('BUILDDIR'),
                                                    name='build_%s_%s' % (imageset.name, img.name),
                                                    description="Building",
                                                    descriptionSuffix=[imageset.name, img.name],
                                                    descriptionDone="Built"))

            self.addStep(steps.ShellCommand(command=store_artifacts_cmd, workdir=util.Property('BUILDDIR'),
                                            name='StoreArtifacts_{}'.format(imageset.name), timeout=None,
                                            description="Storing",
                                            descriptionSuffix=["artifacts", "for", imageset.name],
                                            descriptionDone="Stored"))
