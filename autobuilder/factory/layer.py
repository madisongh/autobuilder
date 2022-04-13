import os
import re

from buildbot.plugins import util, steps
from buildbot.process.factory import BuildFactory
from buildbot.process.results import SKIPPED

from autobuilder.factory.base import datestamp, is_pull_request
from autobuilder.factory.base import extract_env_vars, dict_merge, merge_env_vars


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
                 extra_options=None, submodules=False, other_layers=None, renamed_variables=False):
        BuildFactory.__init__(self)
        if extra_env is None:
            extra_env = {}
        if other_layers is None:
            other_layers = {}
        self.addStep(steps.SetProperty(name='SetDatestamp',
                                       property='datestamp', value=datestamp))

        branchcmd = 'targetbranch="%(prop:basename)s"; [ -n "$targetbranch" ] || targetbranch="%(prop:branch)s";' + \
                    'export targetbranch; pokybranch="%(prop:pokybranch)s";' + \
                    '[ -n "$pokybranch" ] || pokybranch=$(echo "$targetbranch" | cut -d- -f1); ' + \
                    'export pokybranch; %(prop:clean_env_cmd)sprintenv'
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
        dep_args = []
        for othername, other_layer in other_layers.items():
            branchprop = 'targetbranch' if other_layer['use_target_branch'] else 'pokybranch'
            subdir = other_layer['subdir']
            self.addStep(steps.ShellCommand(command=['git', 'clone',
                                                     '--branch', util.Property(branchprop),
                                                     '--depth', '1', other_layer['url'], subdir],
                                            name='{}_clone'.format(othername),
                                            workdir=os.path.join("build", "poky"),
                                            description="Cloning",
                                            descriptionSuffix=other_layer['url'],
                                            descriptionDone="Cloned"))
            if other_layer['sublayers']:
                dep_args += [os.path.join('..', subdir, sub) for sub in other_layer['sublayers']]
            else:
                dep_args.append(os.path.join('..', subdir))
        if dep_args:
            dep_args = ["--no-auto-dependency", "--dependency"] + dep_args
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
        self.addStep(steps.Git(repourl=repourl, submodules=submodules,
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
                    'fi; %(prop:clean_env_cmd)s. oe-init-build-env; bitbake -e | grep "^DISTROOVERRIDES="; printenv'
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

        cmd = "%(prop:clean_env_cmd)syocto-check-layer"
        if extra_options:
            cmd += " " + extra_options
        if machines is None:
            cmd += " --machines qemux86"
        else:
            cmd += " --machines " + " ".join(machines)
        if dep_args:
            cmd += " " + " ".join(dep_args)
        cmd += " -- ../{}".format(layerdir)
        self.addStep(steps.ShellCommand(command=['bash', '-c', util.Interpolate(cmd)], timeout=None,
                                        env=merge_env_vars(extra_env, renamed_variables),
                                        workdir=util.Property('BUILDDIR'),
                                        name='yocto_check_layer',
                                        description="Checking",
                                        descriptionSuffix="layer",
                                        descriptionDone="Checked"))
