"""
Autobuilder configuration class.
"""
from buildbot.buildslave import BuildSlave
from buildbot.buildslave.ec2 import EC2LatentBuildSlave
from buildbot.changes.gitpoller import GitPoller
from buildbot.changes.filter import ChangeFilter
from buildbot.schedulers.basic import SingleBranchScheduler
from buildbot.schedulers.forcesched import ForceScheduler, ChoiceStringParameter
from buildbot.schedulers.triggerable import Triggerable
from buildbot.process import properties
from buildbot.config import BuilderConfig

import factory


ABCFG_DICT = {}


class AutobuilderConfig(object):
    def __init__(self, name, buildslaves, controllers,
                 repos, distros, ec2slaves=None):
        if name in ABCFG_DICT:
            raise RuntimeError('Autobuilder config %s already exists' % name)
        self.name = name
        self._buildslaves = buildslaves
        self.ec2slaves = ec2slaves or {}
        self.ostypes = self._buildslaves.keys()
        self.buildslave_conftext = {}
        for otype in self._buildslaves:
            for bstuple in self._buildslaves[otype]:
                if len(bstuple) > 2:
                    self.buildslave_conftext[bstuple[0]] = bstuple[2]
                else:
                    self.buildslave_conftext[bstuple[0]] = ''
        self.controllers = controllers
        self.repos = repos
        self.distros = distros
        self.distrodict = {d.name: d for d in self.distros}
        for d in self.distros:
            d.set_host_oses(self.ostypes)
        self.codebasemap = {self.repos[r].uri: r for r in self.repos}
        ABCFG_DICT[name] = self

    def codebase_generator(self, change_dict):
        return self.codebasemap[change_dict['repository']]

    @property
    def buildslaves(self):
        controllers = [BuildSlave(bs[0], bs[1], max_builds=1)
                       for bs in self.controllers]
        slaves = [BuildSlave(bs[0], bs[1], max_builds=1)
                  for ostype in self.ostypes
                  for bs in self._buildslaves[ostype]]
        ec2slaves = [EC2LatentBuildSlave(bs[0], bs[1], max_builds=1,
                                         instance_type=bs[2], ami=bs[3])
                     for ostype in self.ostypes
                     for bs in self.ec2slaves[ostype]]
        # noinspection PyTypeChecker
        return controllers + slaves + ec2slaves

    @property
    def change_sources(self):
        return [GitPoller(repourl=self.repos[r].uri,
                          workdir='gitpoller-' + self.repos[r].name,
                          branches=([d.branch for d in self.distros
                                     if d.reponame == r] +
                                    [d.kernelbranches[karch] for d in self.distros
                                     if d.kernelreponame == r
                                     for karch in d.kernelbranches]),
                          pollinterval=self.repos[r].pollinterval,
                          pollAtLaunch=True, project=self.repos[r].project)
                for r in self.repos if self.repos[r].pollinterval]

    @property
    def schedulers(self):
        s = []
        for d in self.distros:
            md_filter = ChangeFilter(project=self.repos[d.reponame].project,
                                     branch=d.branch, codebase=d.reponame)
            s.append(SingleBranchScheduler(name=d.name,
                                           change_filter=md_filter,
                                           treeStableTimer=d.repotimer,
                                           properties={'buildtype': d.default_buildtype},
                                           codebases=d.codebases(self.repos),
                                           createAbsoluteSourceStamps=True,
                                           builderNames=[d.name]))
            if d.kernelreponame is not None:
                for kbranch in d.kernelbranches:
                    kern_filter = ChangeFilter(project=self.repos[d.kernelreponame].project,
                                               branch=d.kernelbranches[kbranch],
                                               codebase=d.kernelreponame)
                    s.append(SingleBranchScheduler(name=d.name + '-kernel-' + kbranch,
                                                   change_filter=kern_filter,
                                                   treeStableTimer=d.repotimer,
                                                   codebases=d.codebases(self.repos),
                                                   createAbsoluteSourceStamps=True,
                                                   properties={'buildtype': d.default_buildtype},
                                                   builderNames=[d.name]))
            for imgset in d.targets:
                name = d.name + '-' + imgset.name
                s += [Triggerable(name=name + '-' + otype,
                                  codebases=d.codebases(self.repos),
                                  properties={'hostos': otype},
                                  builderNames=[name + '-' + otype])
                      for otype in d.host_oses]
            # noinspection PyTypeChecker
            forceprops = ChoiceStringParameter(name='buildtype',
                                               label='Build type',
                                               choices=[bt.name for bt in d.buildtypes],
                                               default=d.default_buildtype)
            s.append(ForceScheduler(name=d.name + '-force',
                                    codebases=d.codebases(self.repos),
                                    properties=[forceprops],
                                    builderNames=[d.name]))
        return s

    @property
    def builders(self):
        b = []
        for d in self.distros:
            props = {'sstate_mirror': d.sstate_mirror,
                     'sstate_mirrorvar': d.sstate_mirrorvar,
                     'dl_mirrorvar': d.dl_mirrorvar or "",
                     'artifacts_path': d.artifacts_path,
                     'downloads_dir': d.dl_dir,
                     'project': self.repos[d.reponame].project,
                     'repourl': self.repos[d.reponame].uri,
                     'branch': d.branch,
                     'setup_script': d.setup_script,
                     'artifacts': ' '.join(d.artifacts),
                     'autobuilder': self.name,
                     'distro': d.name,
                     'buildnum_template': d.buildnum_template,
                     'release_buildname_variable': d.release_buildname_variable}
            b.append(BuilderConfig(name=d.name,
                                   slavenames=[bs[0] for bs in self.controllers],
                                   properties=props.copy(),
                                   factory=factory.DistroBuild(d, self.repos)))
            repo = self.repos[d.reponame]
            for imgset in d.targets:
                b += [BuilderConfig(name=d.name + '-' + imgset.name + '-' + otype,
                                    slavenames=[bs[0] for bs in self._buildslaves[otype]],
                                    properties=props.copy(),
                                    factory=factory.DistroImage(repourl=repo.uri,
                                                                submodules=repo.submodules,
                                                                branch=d.branch,
                                                                codebase=d.reponame,
                                                                imagedict=imgset.images,
                                                                sdkmachines=d.sdkmachines,
                                                                sdktargets=imgset.sdkimages))
                      for otype in d.host_oses]
        return b

@properties.renderer
def kernel_srcrev(props):
    karchs = props.getProperty('kernel_archs').split() or []
    abcfg = ABCFG_DICT[props.getProperty('autobuilder')]
    revisions = props.getProperty('got_revision')
    result = {}
    kbase = abcfg.distrodict[props.getProperty('distro')].kernelreponame
    for karch in karchs:
        try:
            val = revisions[kbase]
        except KeyError:
            val = '${AUTOREV}'
        result[karch] = val
    return result


def buildslave_extraconfig(props):
    buildslave_name = props.getProperty('slavename')
    abcfg = ABCFG_DICT[props.getProperty('autobuilder')]
    return abcfg.buildslave_conftext[buildslave_name]
