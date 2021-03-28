import os
import re

from buildbot.plugins import util, steps
from buildbot.process.factory import BuildFactory
from buildbot.process.results import SKIPPED

from autobuilder.factory.base import datestamp, is_pull_request
from autobuilder.factory.base import extract_env_vars, dict_merge, ENV_VARS


@util.renderer
def make_layercheck_autoconf(props):
    # Worker-specific config
    result = props.getProperty('worker_extraconf', default=[])
    # Distro-specific config
    result += props.getProperty('extraconf', default=[])

    return util.Interpolate('\n'.join(result) + '\n')


def extract_branch_names(_rc, stdout, _stderr):
    pat = re.compile(r'^(targetbranch|pokybranch)=(.*)')
    vardict = {}
    for line in stdout.split('\n'):
        m = pat.match(line)
        if m is not None:
            vardict[m.group(1)] = m.group(2)
    return vardict


class CheckLayer(BuildFactory):
    def __init__(self, repourl, layerdir, pokyurl, codebase='', extra_env=None, machines=None,
                 extra_options=None, submodules=False):
        BuildFactory.__init__(self)
        if extra_env is None:
            extra_env = {}
        self.addStep(steps.SetProperty(name='SetDatestamp',
                                       property='datestamp', value=datestamp))

        branchcmd = 'targetbranch="%(prop:basename)s"; [ -n "$targetbranch" ] || targetbranch="%(prop:branch)s";' + \
                    'export targetbranch; export pokybranch=$(echo "$targetbranch" | cut -d- -f1); printenv'
        self.addStep(steps.SetPropertyFromCommand(command=['bash', '-c', util.Interpolate(branchcmd)],
                                                  env=extra_env or {},
                                                  extract_fn=extract_branch_names,
                                                  name='get_branch_names',
                                                  description="Extracting",
                                                  descriptionSuffix=["branch", "names"],
                                                  descriptionDone="Extracted"))
        self.addStep(steps.ShellCommand(command=['git', 'clone',
                                                 '--branch', util.Property('pokybranch'),
                                                 '--depth', '1', pokyurl,
                                                 'poky'],
                                        name='poky_clone',
                                        description="Cloning",
                                        descriptionSuffix=[pokyurl],
                                        descriptionDone="Cloned"))
        self.addStep(steps.Git(repourl=repourl,
                               workdir=os.path.join("build", "poky", layerdir),
                               submodules=submodules,
                               branch=util.Property('targetbranch'),
                               codebase=codebase,
                               name='checkout_layer',
                               mode='full',
                               method='clobber',
                               doStepIf=lambda step: not is_pull_request(step.build.getProperties()),
                               hideStepIf=lambda results, step: results == SKIPPED))
        if 'github.com' in repourl:
            self.addStep(steps.GitHub(repourl=repourl, submodules=submodules,
                                      workdir=os.path.join("build", "poky", layerdir),
                                      branch=util.Property('branch'), codebase=codebase,
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
