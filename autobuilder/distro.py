"""
Distro configuration class.
"""
from buildtype import Buildtype
from abconfig import ABCFG_DICT
from buildbot.process import properties

DEFAULT_BLDTYPES = ['ci', 'snapshot', 'release']


class Distro(object):
    def __init__(self, name, reponame, branch, email, path, dldir, ssmirror,
                 targets, sdkmachines=None,
                 host_oses=None, setup_script='./setup-env', repotimer=300,
                 artifacts=None,
                 sstate_mirrorvar='SSTATE_MIRRORS = "file://.* file://%s/PATH"',
                 kernelreponame=None, kernelbranches=None, dl_mirrorvar=None,
                 buildtypes=None, buildnum_template='DISTRO_BUILDNUM = "-%s"',
                 release_buildname_variable='DISTRO_BUILDNAME',
                 dl_mirror='file:///dummy/no/such/path'):
        self.name = name
        self.reponame = reponame
        self.branch = branch
        self.email = email
        self.artifacts_path = path
        self.dl_dir = dldir
        self.sstate_mirror = ssmirror
        self.targets = targets
        self.sdkmachines = sdkmachines
        self.host_oses = host_oses
        self.setup_script = setup_script
        self.repotimer = repotimer
        self.artifacts = artifacts
        self.sstate_mirrorvar = sstate_mirrorvar
        self.kernelreponame = kernelreponame
        self.kernelbranches = kernelbranches or {}
        self.dl_mirrorvar = dl_mirrorvar
        self.dl_mirror = dl_mirror
        self.buildnum_template = buildnum_template
        self.release_buildname_variable = release_buildname_variable
        self.buildtypes = buildtypes
        if buildtypes is None:
            self.buildtypes = [Buildtype(bt) for bt in DEFAULT_BLDTYPES]
            self.buildtypes[0].defaulttype = True
        self.btdict = {bt.name: bt for bt in self.buildtypes}
        defaultlist = [bt.name for bt in self.buildtypes if bt.defaulttype]
        if len(defaultlist) != 1:
            raise RuntimeError('Must set exactly one default build type for %s' % self.name)
        self.default_buildtype = defaultlist[0]

    def codebases(self, repos):
        cbdict = {self.reponame: {'repository': repos[self.reponame].uri}}
        if self.kernelreponame:
            cbdict[self.kernelreponame] = {'repository': repos[self.kernelreponame].uri}
        return cbdict

    def set_host_oses(self, default_oses):
        if self.host_oses is None:
            self.host_oses = default_oses


def _get_sdkinfo(props):
    abcfg = ABCFG_DICT[props.getProperty('autobuilder')]
    distro = abcfg.distrodict[props.getProperty('distro')]
    buildtype = props.getProperty('buildtype')
    return distro.btdict[buildtype]


def build_sdk(props):
    return _get_sdkinfo(props).build_sdk


def install_sdk(props):
    return props.getProperty('primary_hostos') and _get_sdkinfo(props).install_sdk


def is_release_build(props):
    return _get_sdkinfo(props).production_release


@properties.renderer
def sdk_root(props):
    root = _get_sdkinfo(props).sdk_root
    if root:
        return '--install-root=' + root
    else:
        return ''


@properties.renderer
def sdk_use_current(props):
    return '--update-current' if _get_sdkinfo(props).current_symlink else ''


@properties.renderer
def sdk_stamp(props):
    if _get_sdkinfo(props).production_release:
        return '--no-stamp'
    else:
        return '--date-stamp=' + props.getProperty('datestamp')
