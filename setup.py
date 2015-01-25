from setuptools import setup, find_packages

setup(
    name='autobuilder',
    version='0.6.0',
    packages=find_packages(),
    license='MIT',
    author='Matt Madison',
    author_email='madison@bliss-m.org',
    install_requires=['buildbot>=0.8.9']
)
