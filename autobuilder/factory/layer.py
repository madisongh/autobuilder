import os

from buildbot.plugins import util, steps
from buildbot.process.factory import BuildFactory
from buildbot.process.results import SKIPPED

from autobuilder.factory.base import worker_extraconfig, datestamp, is_pull_request
from autobuilder.factory.base import extract_env_vars, dict_merge, ENV_VARS


@util.renderer
def make_layercheck_autoconf(props):
    # Worker-specific config
    result = worker_extraconfig(props) or []
    # Distro-specific config
    result += props.getProperty('extraconf') or []

    return util.Interpolate('\n'.join(result) + '\n')


class CheckLayer(BuildFactory):
    def __init__(self, repourl, layerdir, pokyurl, branch='master', pokybranch=None,
                 codebase='', extra_env=None, machines=None,
                 extra_options=None, submodules=False):
        BuildFactory.__init__(self)
        self.addStep(steps.SetProperty(name='SetDatestamp',
                                       property='datestamp', value=datestamp))
        if pokybranch is None:
            pokybranch = branch.split('-')[0]
        self.addStep(steps.ShellCommand(command=['git', 'clone',
                                                 '--branch', pokybranch,
                                                 '--depth', '1', pokyurl,
                                                 'poky'],
                                        name='poky-clone-' + pokybranch,
                                        description="Cloning",
                                        descriptionSuffix=[pokyurl],
                                        descriptionDone="Cloned"))
        self.addStep(steps.Git(repourl=repourl,
                               workdir=os.path.join("build", "poky", layerdir),
                               submodules=submodules,
                               branch=branch,
                               codebase=codebase,
                               name='git-checkout-{}'.format(branch),
                               mode='full',
                               method='clobber',
                               doStepIf=lambda step: not is_pull_request(step.build.getProperties()),
                               hideStepIf=lambda results, step: results == SKIPPED))
        if 'github.com' in repourl:
            self.addStep(steps.GitHub(repourl=repourl, submodules=submodules,
                                      workdir=os.path.join("build", "poky", layerdir),
                                      branch=branch, codebase=codebase,
                                      name='git-checkout-pullrequest-ref',
                                      mode='full',
                                      method='clobber',
                                      doStepIf=lambda step: is_pull_request(step.build.getProperties()),
                                      hideStepIf=lambda results, step: results == SKIPPED))
        # First, remove duplicates from original PATH (saved in ORIGPATH env var),
        # then strip out the virtualenv bin directory if we're in a virtualenv.
        setup_cmd = 'PATH=`echo -n "$ORIGPATH" | awk -v RS=: -v ORS=: \'!arr[$0]++\'`;' + \
                    'if [ -n "$VIRTUAL_ENV" ]; then ' + \
                    'PATH=`echo "$PATH" | sed -re "s,(^|:)$VIRTUAL_ENV/bin(:|$),\\2,g;s,^:,,"`; ' + \
                    'fi; . oe-init-build-env; bitbake -e | grep "^DISTROOVERRIDES="; printenv'
        # Setup steps

        # Clean copy of original PATH, before any setup scripts have been run, to ensure
        # we start fresh before each imageset, when we're running them sequentially.
        self.addStep(steps.SetPropertyFromCommand(command=['bash', '-c', 'export ORIGPATH="$PATH"; printenv'],
                                                  env=extra_env,
                                                  extract_fn=extract_env_vars,
                                                  name='save_path',
                                                  description="Saving",
                                                  descriptionSuffix=["original", "PATH"],
                                                  descriptionDone="Saved"))

        self.addStep(steps.SetPropertyFromCommand(command=['bash', '-c', util.Interpolate(setup_cmd)],
                                                  workdir=os.path.join("build", "poky"),
                                                  env=dict_merge(extra_env,
                                                                 {"ORIGPATH": util.Property("ORIGPATH")}),
                                                  extract_fn=extract_env_vars,
                                                  name='EnvironmentSetup',
                                                  description="Running",
                                                  descriptionSuffix=["setup", "script"],
                                                  descriptionDone="Ran"))
        self.addStep(steps.StringDownload(s=make_layercheck_autoconf, workerdest='auto.conf',
                                          workdir=util.Interpolate("%(prop:BUILDDIR)s/conf"),
                                          name='make-auto.conf',
                                          description="Creating",
                                          descriptionSuffix=["auto.conf"],
                                          descriptionDone="Created"))

        cmd = "yocto-check-layer"
        if extra_options:
            cmd += " " + extra_options
        if machines is None:
            cmd += " --machines qemux86"
        else:
            cmd += " --machines " + " ".join(machines)
        cmd += " -- ../{}".format(layerdir)
        self.addStep(steps.ShellCommand(command=['bash', '-c', cmd], timeout=None,
                                        env=dict_merge(ENV_VARS, extra_env),
                                        workdir=util.Property('BUILDDIR'),
                                        name='yocto_check_layer',
                                        description="Checking",
                                        descriptionSuffix="layer",
                                        descriptionDone="Checked"))
