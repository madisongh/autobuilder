from buildbot.plugins import util
from buildbot.plugins import schedulers
from buildbot.config import BuilderConfig

from autobuilder.abconfig import AutobuilderForceScheduler, AutobuilderConfig
from autobuilder.factory.distro import DistroImage
from autobuilder.factory.base import delete_env_vars
from autobuilder.workers.ec2 import nextEC2Worker


class Buildtype(object):
    force_properties = [
        util.BooleanParameter(name='current_symlink',
                              label='Update current symlink',
                              default=False),
        util.BooleanParameter(name='pullrequest',
                              label='This is a pull request',
                              default=False),
        util.BooleanParameter(name='keep_going',
                              label='Add -k option to bitbake for this build',
                              default=False),
        util.BooleanParameter(name='noartifacts',
                              label='Disable artifacts upload for this build',
                              default=False),
        util.TextParameter(name='buildtype_extraconf',
                           label='auto.conf additions for this build type',
                           default=''),
    ]

    def __init__(self, name, current_symlink=False, defaulttype=False,
                 pullrequesttype=False, keep_going=False, noartifacts=False,
                 extra_config=None):
        self.name = name
        self.defaulttype = defaulttype
        if extra_config is None:
            extra_config = ''
        self.properties = {
            'current_symlink': current_symlink,
            'pullrequest': pullrequesttype,
            'keep_going': keep_going,
            'noartifacts': noartifacts,
            'buildtype_extraconf': '\n'.join(extra_config) if isinstance(extra_config, list) else extra_config
        }


DEFAULT_BLDTYPES = [Buildtype('ci', defaulttype=True),
                    Buildtype('no-sstate',
                              extra_config=['SSTATE_MIRRORS_forcevariable = ""']),
                    Buildtype('pr', pullrequesttype=True, noartifacts=True,
                              extra_config=['INHERIT_remove = "buildhistory"'])]


class ImageSpec(object):
    def __init__(self, name=None, machine=None, sdkmachine=None):
        self.name = name
        self.machine = machine
        self.sdkmachine = sdkmachine
        if not machine and not sdkmachine:
            raise ValueError("ImageSpec with no MACHINE or SDKMACHINE setting")
        self.mcname = machine if machine else 'none-' + sdkmachine
        self.is_sdk = False


class TargetImage(ImageSpec):
    def __init__(self, machine, args, name=None):
        if isinstance(args, str):
            self.args = args.split()
        else:
            self.args = args
        if not name:
            name = machine + ':' + '_'.join([a for a in self.args if not a.startswith('-')])
        super().__init__(name, machine=machine)


class SdkImage(ImageSpec):
    def __init__(self, machine, sdkmachine, args, name=None):
        if isinstance(args, str):
            self.args = args.split()
        else:
            self.args = args
        if not name:
            if machine:
                name = 'SDK_%s:%s:%s' % (sdkmachine, machine,
                                         '_'.join([a for a in self.args if not a.startswith('-')]))
            else:
                name = 'SDK_%s:%s' % (sdkmachine, '_'.join([a for a in self.args if not a.startswith('-')]))
        super().__init__(name, machine, sdkmachine)
        self.is_sdk = True


class TargetImageSet(object):
    def __init__(self, name, imagespecs=None, multiconfig=False, distro=None, artifacts=None):
        self.name = name
        self.distro = distro
        self.multiconfig = multiconfig
        self.artifacts = artifacts
        if imagespecs is None:
            raise RuntimeError('No images defined for %s' % name)
        self.imagespecs = imagespecs


class WeeklySlot(object):
    def __init__(self, day, hour, minute):
        self.dayOfWeek = day
        self.hour = hour
        self.minute = minute


class Distro(object):
    WEEKLY_SLOTS = [WeeklySlot(d, h, 0) for d in [5, 6] for h in [4, 8, 12, 16, 20]]
    LAST_USED_WEEKLY = -1

    @classmethod
    def get_weekly_slot(cls):
        try:
            slot = cls.WEEKLY_SLOTS[cls.LAST_USED_WEEKLY + 1]
            cls.LAST_USED_WEEKLY += 1
        except IndexError:
            raise RuntimeError('too many weekly builds scheduled')
        return slot

    def __init__(self, name, reponame, branch, email, path,
                 targets=None,
                 setup_script='./setup-env',
                 repotimer=300,
                 artifacts=None,
                 buildtypes=None,
                 weekly_type=None,
                 push_type='__default__',
                 pullrequest_type=None,
                 extra_config=None,
                 extra_env=None,
                 parallel_builders=False,
                 worker_prefix=None,
                 renamed_variables=False):
        self.name = name
        self.reponame = reponame
        self.branch = branch
        self.email = email
        self.artifacts_path = path
        self.targets = targets
        self.setup_script = setup_script
        self.repotimer = repotimer
        if isinstance(artifacts, str):
            self.artifacts = [a.strip() for a in artifacts.split(',')]
        else:
            self.artifacts = artifacts or []
        self.buildtypes = buildtypes
        self.buildtypes = buildtypes or DEFAULT_BLDTYPES
        self.btdict = {bt.name: bt for bt in self.buildtypes}
        defaultlist = [bt.name for bt in self.buildtypes if bt.defaulttype]
        if len(defaultlist) != 1:
            raise RuntimeError('Must set exactly one default build type for %s' % self.name)
        self.default_buildtype = defaultlist[0]
        if weekly_type is not None and weekly_type not in self.btdict.keys():
            raise RuntimeError('Weekly build type for %s set to unknown type: %s' % (self.name, weekly_type))
        self.weekly_type = weekly_type
        if push_type:
            self.push_type = push_type if push_type != '__default__' else self.default_buildtype
        else:
            self.push_type = None
        if pullrequest_type:
            bt = self.btdict[pullrequest_type]
            if not bt.properties['pullrequest']:
                raise RuntimeError('Build type %s must have pullrequest property' % pullrequest_type)
            self.pullrequest_type = bt.name
        else:
            self.pullrequest_type = None
        if extra_config:
            self.extra_config = [extra_config] if isinstance(extra_config, str) else extra_config
        else:
            self.extra_config = []
        self.renamed_variables = renamed_variables
        self.extra_env = extra_env
        self.parallel_builders = parallel_builders
        self.worker_prefix = worker_prefix
        self.abconfig = None
        self._builders = None
        self._schedulers = None

    def codebases(self, repos):
        cbdict = {self.reponame: {'repository': repos[self.reponame].uri}}
        return cbdict

    def codebaseparamlist(self, repos):
        return [util.CodebaseParameter(codebase=self.reponame,
                                       repository=util.FixedParameter(name='repository',
                                                                      default=repos[self.reponame].uri),
                                       branch=util.FixedParameter(name='branch', default=self.branch))]

    def builders(self, abcfg: AutobuilderConfig):
        if self._builders is None:
            repo = abcfg.repos[self.reponame]
            props = {
                'artifacts_path': self.artifacts_path,
                'project': self.name,
                'repourl': repo.uri,
                'branch': self.branch,
                'setup_script': self.setup_script,
                'autobuilder': self.abconfig,
                'distro': self.name,
                'extraconf': self.extra_config or [],
                'clean_env_cmd': delete_env_vars(self.renamed_variables),
            }
            if self.artifacts:
                props['artifacts'] = self.artifacts
            if self.worker_prefix:
                workernames = [wname for wname in abcfg.worker_names if wname.startswith(self.worker_prefix)]
            else:
                workernames = abcfg.worker_names
            if self.parallel_builders:
                self._builders = [BuilderConfig(name=self.name + '-' + imgset.name,
                                                workernames=workernames,
                                                nextWorker=nextEC2Worker,
                                                properties=props,
                                                factory=DistroImage(repourl=repo.uri,
                                                                    submodules=repo.submodules,
                                                                    branch=self.branch,
                                                                    codebase=self.reponame,
                                                                    imagesets=[imgset],
                                                                    extra_env=self.extra_env,
                                                                    renamed_variables=self.renamed_variables))
                                  for imgset in self.targets]
            else:
                self._builders = [BuilderConfig(name=self.name,
                                                workernames=workernames,
                                                nextWorker=nextEC2Worker,
                                                properties=props,
                                                factory=DistroImage(repourl=repo.uri,
                                                                    submodules=repo.submodules,
                                                                    branch=self.branch,
                                                                    codebase=self.reponame,
                                                                    imagesets=self.targets,
                                                                    extra_env=self.extra_env,
                                                                    renamed_variables=self.renamed_variables))]
        return self._builders

    def schedulers(self, abcfg: AutobuilderConfig):
        if self._schedulers is None:
            repos = abcfg.repos
            s = []
            if self.parallel_builders:
                builder_names = [self.name + '-' + imgset.name for imgset in self.targets]
            else:
                builder_names = [self.name]
            if self.push_type is not None:
                md_filter = util.ChangeFilter(project=self.name,
                                              branch=self.branch, codebase=self.reponame,
                                              category=['push'])
                props = {'buildtype': self.push_type}
                props.update(self.btdict[self.push_type].properties)
                s.append(schedulers.SingleBranchScheduler(name=self.name,
                                                          change_filter=md_filter,
                                                          treeStableTimer=self.repotimer,
                                                          properties=props,
                                                          codebases=self.codebases(repos),
                                                          createAbsoluteSourceStamps=True,
                                                          builderNames=builder_names))
            if self.pullrequest_type is not None:
                props = {'buildtype': self.pullrequest_type}
                props.update(self.btdict[self.pullrequest_type].properties)
                s.append(schedulers.SingleBranchScheduler(name=self.name + '-pr',
                                                          change_filter=util.ChangeFilter(project=self.name,
                                                                                          codebase=self.reponame,
                                                                                          category=['pull']),
                                                          properties=props,
                                                          codebases=self.codebases(repos),
                                                          createAbsoluteSourceStamps=True,
                                                          builderNames=builder_names))
            # noinspection PyTypeChecker
            forceprops = [util.ChoiceStringParameter(name='buildtype',
                                                     label='Build type',
                                                     choices=[bt.name for bt in self.buildtypes],
                                                     autopopulate={bt.name: bt.properties
                                                                   for bt in self.buildtypes},
                                                     default=self.default_buildtype)]
            forceprops += self.btdict[self.default_buildtype].force_properties
            s.append(AutobuilderForceScheduler(name=self.name + '-force',
                                               codebases=self.codebaseparamlist(repos),
                                               properties=forceprops,
                                               builderNames=builder_names))
            if self.weekly_type is not None:
                slot = self.get_weekly_slot()
                props = {'buildtype': self.weekly_type}
                props.update(self.btdict[self.weekly_type].properties)
                s.append(schedulers.Nightly(name=self.name + '-' + 'weekly',
                                            properties=props,
                                            codebases=self.codebases(repos),
                                            createAbsoluteSourceStamps=True,
                                            builderNames=builder_names,
                                            dayOfWeek=slot.dayOfWeek,
                                            hour=slot.hour,
                                            minute=slot.minute))
            self._schedulers = s
        return self._schedulers
