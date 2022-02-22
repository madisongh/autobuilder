import re
import time

from buildbot.plugins import util

import autobuilder.abconfig as abconfig

base_env_vars = {'PATH': util.Property('PATH'),
                 'ORIGPATH': util.Property('ORIGPATH'),
                 'BUILDDIR': util.Property('BUILDDIR'),
                 }

var_rename_mapping = {
    'BB_ENV_EXTRAWHITE': 'BB_ENV_PASSTHROUGH_ADDITIONS'
}


def dict_merge(*dict_args):
    result = {}
    for d in dict_args:
        if d:
            result.update(d)
    return result


def is_pull_request(props):
    return props.getProperty('pullrequest', default=False)


def extract_env_vars(rc, stdout, stderr):
    pat = re.compile('^(' + '|'.join(base_env_vars.keys()) + '|DISTROOVERRIDES)=(.*)')
    vardict = {}
    for line in stdout.split('\n'):
        m = pat.match(line)
        if m is not None:
            if m.group(1) in ["BB_ENV_EXTRAWHITE", "BB_ENV_PASSTHROUGH_ADDITIONS"]:
                envvars = m.group(2).split()
                if "BBMULTICONFIG" not in envvars:
                    envvars.append("BBMULTICONFIG")
                vardict[m.group(1)] = ' '.join(envvars)
            elif m.group(1) == "DISTROOVERRIDES":
                val = m.group(2).strip('"')
                if ':' not in val:
                    vardict["DISTRO"] = val
            else:
                vardict[m.group(1)] = m.group(2)
    return vardict


def merged_env_vars(props, extra_env):
    varnames = props.getProperty('varnames', 'old')
    if varnames == 'old':
        return dict_merge(extra_env, renamed_env_vars[varnames])
    additional = {}
    for oldname, newname in var_rename_mapping.items():
        if oldname in extra_env:
            if newname not in extra_env:
                extra_env[newname] = extra_env[oldname]
                del extra_env[oldname]
        oldprop = props.getProperty(oldname, default=None)
        newprop = props.getProperty(newname, default=None)
        if oldprop is not None and newprop is not None:
            additional[newname] = oldprop
        else:
            additional[newname] = newprop
    return dict_merge(base_env_vars, additional, extra_env)


@util.renderer
def datestamp(props):
    return str(time.strftime("%Y%m%d"))
