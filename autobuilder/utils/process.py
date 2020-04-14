# ex:ts=4:sw=4:sts=4:et
# -*- tab-width: 4; c-basic-offset: 4; indent-tabs-mode: nil -*-
#
# process.py
#
# Module that supports the use of subprocesses for running
# commands and processing the output of those commands.
#
# Much of this is borrowed from bitbake.
#

import sys
import subprocess
import signal


def subproc_preexec():
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)


class CmdError(RuntimeError):
    def __init__(self, command, msg=None):
        self.command = command
        self.msg = msg

    # noinspection PyUnboundLocalVariable
    def __str__(self):
        if not isinstance(self.command, str):
            cmd = subprocess.list2cmdline(self.command)
        else:
            cmd = self.command

        msg = "Execution of '%s' failed" % cmd
        if self.msg:
            msg += ': %s' % self.msg
        return msg


class NotFoundError(CmdError):
    def __str__(self):
        return CmdError.__str__(self) + ": command not found"


class ExecutionError(CmdError):
    def __init__(self, command, exitcode, stdout=None, stderr=None):
        CmdError.__init__(self, command)
        self.exitcode = exitcode
        self.stdout = stdout
        self.stderr = stderr

    def __str__(self):
        message = ""
        if self.stderr:
            message += self.stderr
        if self.stdout:
            message += self.stdout
        if message:
            message = ":\n" + message
        return (CmdError.__str__(self) +
                " with exit code %s" % self.exitcode + message)


class Popen(subprocess.Popen):
    defaults = {
        "close_fds": True,
        "preexec_fn": subproc_preexec,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.PIPE,
        "shell": False,
    }

    def __init__(self, *args, **kwargs):
        options = dict(self.defaults)
        options.update(kwargs)
        subprocess.Popen.__init__(self, *args, **options)


def run(cmd, input=None, errignore=False, **options):
    """Convenience function to run a command and return its output, raising an
    exception when the command fails"""

    if isinstance(cmd, str) and "shell" not in options:
        options["shell"] = True

    try:
        pipe = Popen(cmd, **options)
    except OSError as exc:
        if exc.errno == 2:
            raise NotFoundError(cmd)
        else:
            raise CmdError(cmd, exc)

    stdout, stderr = pipe.communicate(input)
    if stdout is not None:
        stdout = stdout.decode("utf-8")
    if stderr is not None:
        stderr = stderr.decode("utf-8")

    if pipe.returncode != 0 and not errignore:
        raise ExecutionError(cmd, pipe.returncode, stdout, stderr)
    return stdout, stderr
