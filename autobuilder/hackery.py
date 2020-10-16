from abc import ABCMeta
import twisted
from twisted.mail.smtp import ESMTPSender, ESMTPSenderFactory


class myESMTPSender(ESMTPSender, metaclass=ABCMeta):
    def _getContextFactory(self):
        if self.context is not None:
            return self.context
        try:
            from twisted.internet import ssl
        except ImportError:
            return None
        else:
            try:
                context = ssl.ClientContextFactory()
                return context
            except AttributeError:
                return None


if twisted.version.major == 20 and twisted.version.minor <= 3:
    ESMTPSenderFactory.protocol = myESMTPSender
