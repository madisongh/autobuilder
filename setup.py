from setuptools import setup, find_packages

setup(
    name='autobuilder',
    version='0.6.7',
    packages=find_packages(),
    license='MIT',
    author='Matt Madison',
    author_email='matt@madison.systems',
    install_requires=['buildbot>=0.8.12m1']
)
