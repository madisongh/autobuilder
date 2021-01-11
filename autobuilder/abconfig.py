"""
Autobuilder configuration class.
"""
from twisted.internet import defer
from buildbot.plugins import changes, schedulers, worker
from autobuilder.workers.config import AutobuilderEC2Worker
from autobuilder.workers.ec2 import MyEC2LatentWorker

ABCFG_DICT = {}


def get_config_for_builder(name):
    return ABCFG_DICT[name]


def set_config_for_builder(name, val):
    ABCFG_DICT[name] = val


def settings_dict():
    return ABCFG_DICT


class Repo(object):
    def __init__(self, name, uri, pollinterval=None,
                 submodules=False):
        self.name = name
        self.uri = uri
        self.pollinterval = pollinterval
        self.submodules = submodules


class AutobuilderForceScheduler(schedulers.ForceScheduler):
    # noinspection PyUnusedLocal,PyPep8Naming,PyPep8Naming
    @defer.inlineCallbacks
    def computeBuilderNames(self, builderNames=None, builderid=None):
        yield defer.returnValue(self.builderNames)


class AutobuilderConfig(object):
    def __init__(self, name, workers, repos, distros, layers):
        if name in settings_dict():
            raise RuntimeError('Autobuilder config {} already exists'.format(name))
        self.name = name
        self.workers = workers
        self.worker_cfgs = {w.name: w for w in self.workers}
        self.worker_names = [w.name for w in self.workers]

        self.repos = repos
        self.distros = distros or []
        self.layers = layers or []
        for layer in self.layers:
            layer.abconfig = self.name
        self.distrodict = {d.name: d for d in self.distros}
        self.layerdict = {layer.name: layer for layer in self.layers}
        for d in self.distros:
            d.abconfig = self.name
        self.codebasemap = {self.repos[r].uri: r for r in self.repos}
        set_config_for_builder(name, self)
        self._builders = None
        self._schedulers = None

    def codebase_generator(self, change_dict):
        return self.codebasemap[change_dict['repository']]

    @property
    def change_sources(self):
        pollers = []
        for r in self.repos:
            if self.repos[r].pollinterval:
                branches = set()
                for d in self.distros:
                    if d.reponame == r and d.push_type:
                        branches.add(d.branch)
                for layer in self.layers:
                    if layer.reponame == r and layer.push_type:
                        branches.update(set(layer.branches))
                pollers.append(changes.GitPoller(self.repos[r].uri,
                                                 workdir='gitpoller-' + self.repos[r].name,
                                                 branches=sorted(branches),
                                                 category='push',
                                                 pollinterval=self.repos[r].pollinterval,
                                                 pollAtLaunch=True))
        return pollers

    @property
    def schedulers(self):
        if self._schedulers is None:
            self._schedulers = []
            for layer in self.layers:
                self._schedulers += layer.schedulers
            for d in self.distros:
                self._schedulers += d.schedulers
        return self._schedulers

    @property
    def builders(self):
        if self._builders is None:
            self._builders = []
            for layer in self.layers:
                self._builders += layer.builders

            for d in self.distros:
                self._builders += d.builders
        return self._builders

    @property
    def all_builder_names(self):
        return sorted([b.name for b in self.builders])

    @property
    def non_pr_scheduler_names(self):
        return sorted([s.name for s in self.schedulers if not s.name.endswith('-pr')])
