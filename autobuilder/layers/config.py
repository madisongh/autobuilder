import os
import urllib.parse

from buildbot.plugins import util
from buildbot.config import BuilderConfig
from buildbot.plugins import schedulers

from abconfig import AutobuilderForceScheduler, ABCFG_DICT
from github.handler import layer_pr_filter
from factory import CheckLayer
from workers.ec2 import nextEC2Worker


class Layer(object):
    def __init__(self, name, reponame, pokyurl, branches, email,
                 repotimer=300,
                 pullrequests=False,
                 layerdir=None,
                 machines=None,
                 extra_config=None,
                 extra_env=None,
                 extra_options=None):
        self.name = name
        self.reponame = reponame
        self.pokyurl = pokyurl
        self.branches = branches
        self.email = email
        self._layerdir = layerdir
        self.repotimer = repotimer
        self.pullrequests = pullrequests
        self.machines = machines or ['qemux86']
        self.extra_config = extra_config
        self.extra_env = extra_env
        self.extra_options = extra_options
        self.abconfig = None
        self._builders = None
        self._schedulers = None

    def layerdir(self, url):
        if self._layerdir:
            return self._layerdir
        return os.path.splitext(os.path.basename(urllib.parse.urlparse(url).path))[0]

    def codebases(self, repos):
        cbdict = {self.reponame: {'repository': repos[self.reponame].uri}}
        return cbdict

    def codebaseparamlist(self, repos):
        return [util.CodebaseParameter(codebase=self.reponame,
                                       repository=util.FixedParameter(name='repository',
                                                                      default=repos[self.reponame].uri),
                                       branch=util.ChoiceStringParameter(name='branch',
                                                                         choices=self.branches,
                                                                         default=self.branches[0]))]

    @property
    def builders(self):
        if self._builders is None:
            abcfg = ABCFG_DICT[self.abconfig]
            repo = abcfg.repos[self.reponame]
            self._builders = [
                BuilderConfig(name=self.name + '-checklayer',
                              workernames=abcfg.worker_names,
                              nextWorker=nextEC2Worker,
                              properties=dict(project=self.name, repourl=repo.uri, autobuilder=self.abconfig,
                                              extraconf=self.extra_config or []),
                              factory=CheckLayer(
                                  repourl=repo.uri,
                                  layerdir=self.layerdir(repo.uri),
                                  submodules=repo.submodules,
                                  pokyurl=self.pokyurl,
                                  codebase=self.reponame,
                                  extra_env=self.extra_env,
                                  machines=self.machines,
                                  extra_options=self.extra_options))
            ]
        return self._builders

    @property
    def schedulers(self):
        if self._schedulers is None:
            repos = ABCFG_DICT[self.abconfig].repos
            self._schedulers = [
                schedulers.AnyBranchScheduler(
                    name=self.name + '-checklayer',
                    change_filter=util.ChangeFilter(project=self.name,
                                                    branch=self.branches,
                                                    category=['push']),
                    treeStableTimer=self.repotimer,
                    codebases=self.codebases(repos),
                    builderNames=[self.name + '-checklayer']),
                AutobuilderForceScheduler(
                    name=self.name + '-checklayer-force',
                    codebases=self.codebaseparamlist(repos),
                    builderNames=[self.name + '-checklayer'])
            ]
            if self.pullrequests:
                self._schedulers.append(schedulers.AnyBranchScheduler(
                    name=self.name + '-checklayer-pr',
                    change_filter=util.ChangeFilter(filter_fn=layer_pr_filter),
                    properties={'pullrequest': True},
                    treeStableTimer=self.repotimer,
                    codebases=self.codebases(repos),
                    builderNames=[self.name + '-checklayer']))
        return self._schedulers
