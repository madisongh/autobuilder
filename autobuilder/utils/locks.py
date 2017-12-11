# Copyright (c) 2017 Matthew Madison
# Distributed under license

import os
import fcntl


def lockfile(name, shared=False):
    """
    lockfile: take out a file-based lock
    :param name: name of file
    :param shared: take out a shared, rather than exclusive, lock (default: False)
    :return: object to pass to unlockfile
    """
    dirname = os.path.dirname(name)
    if not os.path.exists(dirname):
        os.makedirs(dirname)
    f = None
    # noinspection PyBroadException
    try:
        f = open(name, 'a+')
        fno = f.fileno()
        fcntl.flock(fno, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        stat1 = os.fstat(fno)
        if os.path.exists(f.name):
            stat2 = os.stat(f.name)
            if stat1.st_ino == stat2.st_ino:
                return f
        f.close()
    except Exception:
        # noinspection PyBroadException
        try:
            f.close()
        except Exception:
            pass
        pass
    return None


def unlockfile(f):
    """
    unlockfile: unlock file-based lock taken with lockfile
    :param f: object returned by lockfile
    :return: void
    """
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.unlink(f.name)
    except (IOError, OSError):
        pass
    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    f.close()
