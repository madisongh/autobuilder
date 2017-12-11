"""
logutils

Simplified logging interface, implementing a Log class with
methods similar to those used in bitbake.  Supports StreamHandler
output only.

Note that setting a debug level implies verbose as well.

To use:
from logutils import Log
log = Log(...)

"""

import logging
import sys


class Log:
    """
    Class for implementing the simplified logging interface.    
    """

    def __init__(self, name):
        """
        Initializer, sets up the underlying logging handlers.        
        """
        self.mylog = logging.getLogger(name)
        self.handler = logging.StreamHandler()
        self.formatter = MyFormatter('%(levelname)s: %(message)s')
        self.handler.setFormatter(self.formatter)
        self.mylog.addHandler(self.handler)
        self.mylog.setLevel(logging.INFO)
        self.handler.setLevel(logging.INFO)
        self.debug_level = 0
        self.verbosity = False

    def set_level(self, debug_level, verbose=False):
        """
        Sets the logging level.
        """
        self.debug_level = debug_level
        self.verbosity = verbose
        level = logging.INFO
        if debug_level > 4:
            level = logging.DEBUG - 3
        elif debug_level > 0:
            level = logging.DEBUG - debug_level + 1
        elif verbose:
            level = logging.INFO - 1
        self.mylog.setLevel(level)
        self.handler.setLevel(level)

    def get_level(self):
        """
        Returns the debug level and verobsity setting
        """
        return self.debug_level, self.verbosity

    def plain(self, *args):
        """
        Plain output, not subject to verobsity or debug settings.
        """
        self.mylog.log(logging.INFO + 1, *args)

    def note(self, *args):
        """
        Plain output, prefixed by Note:, not subject
        to verbosity or debug settings.
        """
        self.mylog.info(*args)

    def verbose(self, *args):
        """
        Logs output only when verbosity is enabled.
        """
        self.mylog.log(logging.INFO - 1, *args)

    def debug(self, level, *args):
        """
        Logs output only when the current debug level
        is >= the level specified in the call.
        """
        if isinstance(level, basestring):
            args = (level,) + args
            level = 1
        self.mylog.log(logging.DEBUG - level + 1, *args)

    def warn(self, *args):
        """
        Logs a warning.  Not subject to debug/verbosity
        settings.
        """
        self.mylog.warning(*args)

    def error(self, *args):
        """
        Logs an error.  Not subject to debug/verbosity
        settings.
        """
        self.mylog.error(*args)

    def fatal(self, *args):
        """
        Logs a fatal error and exits.
        """
        self.mylog.critical(*args)
        sys.exit(1)


class MyFormatter(logging.Formatter):
    """
    Logging formatter that adds 'verbose', 'plain', and extra debug levels.
    """

    CRITICAL = logging.CRITICAL
    ERROR = logging.ERROR
    WARNING = logging.WARNING
    PLAIN = logging.INFO + 1
    NOTE = logging.INFO
    VERBOSE = logging.INFO - 1
    DEBUG = logging.DEBUG
    DEBUG2 = logging.DEBUG - 1
    DEBUG3 = logging.DEBUG - 2
    DEBUG4 = logging.DEBUG - 3

    levelnames = {
        CRITICAL: 'FATAL ERROR',
        ERROR: 'ERROR',
        WARNING: 'Warning',
        PLAIN: '',
        NOTE: 'Note',
        VERBOSE: 'Note',
        DEBUG: '[debug]',
        DEBUG2: '[debug]',
        DEBUG3: '[debug]',
        DEBUG4: '[debug]'
    }

    def get_level_name(self, levelno):
        """
        Translates a level number to a name string.
        """
        try:
            return self.levelnames[levelno]
        except KeyError:
            return 'LogLevel=%d' % levelno

    def format(self, record):
        if record.levelno == self.PLAIN:
            return record.getMessage()
        record.levelname = self.get_level_name(record.levelno)
        return logging.Formatter.format(self, record)
