"""
Build type information
"""


class Buildtype(object):
    def __init__(self, name, build_sdk=False, install_sdk=False,
                 sdk_root=None, current_symlink=False, defaulttype=False,
                 production_release=False):
        self.name = name
        self.build_sdk = build_sdk
        self.install_sdk = install_sdk
        self.sdk_root = sdk_root
        self.current_symlink = current_symlink
        self.defaulttype = defaulttype
        self.production_release = production_release
