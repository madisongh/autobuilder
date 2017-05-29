from setuptools import setup, find_packages

setup(
    name='autobuilder',
    version='0.10.0',
    packages=find_packages(),
    license='MIT',
    author='Matt Madison',
    author_email='matt@madison.systems',
    install_requires=['buildbot', 'boto']
)
