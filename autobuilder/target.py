"""
Target information classes.
"""


class TargetImageSet(object):
    def __init__(self, name, images=None, sdkimages=None):
        self.name = name
        if images is None and sdkimages is None:
            raise RuntimeError('No images or SDK images defined for %s' %
                               name)
        self.images = images
        self.sdkimages = sdkimages
