import os
import time

import boto3
import botocore
import botocore.exceptions

from autobuilder.utils.logutils import Log


class S3Session(object):
    def __init__(self, logger=None, bucket=None):
        self.s3client = None
        if logger is None:
            self.log = Log(name=__name__)
        else:
            self.log = logger
        self.bucket = bucket
        self.makeclient()

    def makeclient(self):
        self.s3client = boto3.Session().client('s3')

    def upload(self, Filename, Key):
        if self.s3client is None:
            self.makeclient()
        try:
            self.s3client.upload_file(Bucket=self.bucket, Key=Key, Filename=Filename)
        except botocore.exceptions.ClientError as e:
            err = e.response['Error']
            self.log.warn("{}/{}: {} {}".format(self.bucket, Key, err['Code'], err['Message']))
            return False
        return True

    def download(self, Key, Filename, quiet=True):
        if self.s3client is None:
            self.makeclient()
        try:
            info = self.s3client.head_object(Bucket=self.bucket, Key=Key)
            self.s3client.download_file(Bucket=self.bucket, Key=Key, Filename=Filename)
            if 'LastModified' in info:
                mtime = int(time.mktime(info['LastModified'].timetuple()))
                os.utime(Filename, (mtime, mtime))
        except botocore.exceptions.ClientError as e:
            err = e.response['Error']
            if quiet and err['Code'] == "404":
                self.log.debug(2, "not found: {}/{}".format(self.bucket, Key))
            else:
                self.log.warn("{}/{}: {} {}".format(self.bucket, Key, err['Code'], err['Message']))
            return False
        except OSError as e:
            if quiet:
                pass
            self.log.warn("os.utime({}): {} (errno {})".format(Filename, e.strerror, e.errno))
            return False
        return True

    def get_object_info(self, Key, quiet=True):
        if self.s3client is None:
            self.makeclient()
        try:
            info = self.s3client.head_object(Bucket=self.bucket, Key=Key)
        except botocore.exceptions.ClientError as e:
            err = e.response['Error']
            if quiet and err['Code'] == "404":
                self.log.debug(2, "not found: {}/{}".format(self.bucket, Key))
            else:
                self.log.warn("{}/{}: {} {}".format(self.bucket, Key, err['Code'], err['Message']))
            return None
        return info
