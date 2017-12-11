#!/usr/bin/env python
# Utility script for use with older versions of OE-Core
# that do not place images in a ${MACHINE} subdirectory.

import os
import shutil
import sys


def main():
    if not os.path.isdir('tmp/deploy/images'):
        return 0
    machine = os.getenv('MACHINE')
    if machine is None:
        return 0
    machdir = os.path.join('tmp/deploy/images', machine)
    if os.path.isdir(machdir):
        return 0
    os.mkdir(machdir)
    files = os.listdir('tmp/deploy/images')
    for f in files:
        path = os.path.join('tmp/deploy/images', f)
        if os.path.isdir(path):
            continue
        print "Moving %s to %s" % (path, machdir)
        shutil.move(path, machdir)
    return 0


if __name__ == "__main__":
    # noinspection PyBroadException
    try:
        ret = main()
        sys.exit(ret)
    except SystemExit:
        pass
    except Exception:
        import traceback

        traceback.print_exc(5)
        sys.exit(1)
