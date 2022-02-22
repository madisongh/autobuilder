import re
import time

from buildbot.plugins import util

import autobuilder.abconfig as abconfig

ENV_VARS = {'PATH': util.Property('PATH'),
            'ORIGPATH': util.Property('ORIGPATH'),
            'BUILDDIR': util.Property('BUILDDIR'),
            }

old_var_names = ['BB_ENV_EXTRAWHITE']
new_var_names = ['BB_ENV_PASSTHROUGH_ADDITIONS']


def dict_merge(*dict_args):
    result = {}
    for d in dict_args:
        if d:
            result.update(d)
    return result


def is_pull_request(props):
    return props.getProperty('pullrequest', default=False)


def extract_env_vars(rc, stdout, stderr):
    pat = re.compile('^(' + '|'.join(list(ENV_VARS.keys()) + old_var_names + new_var_names) + '|DISTROOVERRIDES)=(.*)')
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


def merge_env_vars(extra_env, renamed_variables):
    oldornewvars = {}
    for v in new_var_names if renamed_variables else old_var_names:
        oldornewvars[v] = util.Property(v)
    return dict_merge(ENV_VARS, oldornewvars, extra_env)


def delete_env_vars(use_new_vars):
    if use_new_vars:
        return 'unset {}; '.format(' '.join(old_var_names))
    else:
        return ''


@util.renderer
def datestamp(props):
    return str(time.strftime("%Y%m%d"))
