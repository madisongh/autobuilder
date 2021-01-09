import re
import time

from buildbot.plugins import util

import autobuilder.abconfig as abconfig

ENV_VARS = {'PATH': util.Property('PATH'),
            'ORIGPATH': util.Property('ORIGPATH'),
            'BB_ENV_EXTRAWHITE': util.Property('BB_ENV_EXTRAWHITE'),
            'BUILDDIR': util.Property('BUILDDIR')
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
    pat = re.compile('^(' + '|'.join(ENV_VARS.keys()) + '|DISTROOVERRIDES)=(.*)')
    vardict = {}
    for line in stdout.split('\n'):
        m = pat.match(line)
        if m is not None:
            if m.group(1) == "BB_ENV_EXTRAWHITE":
                envvars = m.group(2).split()
                if "BBMULTICONFIG" not in envvars:
                    envvars.append("BBMULTICONFIG")
                vardict["BB_ENV_EXTRAWHITE"] = ' '.join(envvars)
            elif m.group(1) == "DISTROOVERRIDES":
                val = m.group(2).strip('"')
                if ':' not in val:
                    vardict["DISTRO"] = val
            else:
                vardict[m.group(1)] = m.group(2)
    return vardict


def worker_extraconfig(props):
    abcfg = abconfig.get_config_for_builder(props.getProperty('autobuilder'))
    wcfg = abcfg.worker_cfgs[props.getProperty('workername')]
    if wcfg:
        return wcfg.conftext
    return None


@util.renderer
def datestamp(props):
    return str(time.strftime("%Y%m%d"))
