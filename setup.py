from setuptools import setup, find_packages

setup(
    name='autobuilder',
    version='0.9.1',
    packages=find_packages(),
    license='MIT',
    author='Matt Madison',
    author_email='matt@madison.systems',
    install_requires=['buildbot>=0.8.12,<0.9', 'boto']
)
