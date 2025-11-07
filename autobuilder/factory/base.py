import re
import time

from buildbot.plugins import util

ENV_VARS = {'PATH': util.Property('PATH'),
            'ORIGPATH': util.Property('ORIGPATH'),
            'BUILDDIR': util.Property('BUILDDIR'),
            'BB_ENV_PASSTHROUGH_ADDITIONS': util.Property('BB_ENV_PASSTHROUGH_ADDITIONS'),
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
            if m.group(1) == "BB_ENV_PASSTHROUGH_ADDITIONS":
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


def merge_env_vars(extra_env):
    return dict_merge(ENV_VARS, extra_env)


def delete_env_vars():
    # This had been used for deleting environment variables
    # that would cause errors after they were used after
    # a major rename in OE. Leaving it here in case that
    # kind of thing happens again.
    return ''


@util.renderer
def datestamp(props):
    return str(time.strftime("%Y%m%d"))
