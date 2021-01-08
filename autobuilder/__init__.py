from .hackery import myESMTPSender
import twisted
from twisted.mail.smtp import ESMTPSenderFactory

if twisted.version.major == 20 and twisted.version.minor <= 3:
    ESMTPSenderFactory.protocol = myESMTPSender
