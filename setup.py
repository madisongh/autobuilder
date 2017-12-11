from setuptools import setup, find_packages

setup(
    name='autobuilder',
    version='0.11.7',
    packages=find_packages(),
    license='MIT',
    author='Matt Madison',
    author_email='matt@madison.systems',
    entry_points={
        'console_scripts': [
            'update-sstate-mirror = autobuilder.scripts.update_sstate_mirror:main',
            'update-downloads = autobuilder.scripts.update_downloads:main',
            'move-images = autobuilder.scripts.moveimages:main',
            'install-sdk = autobuilder.scripts.install_sdk:main',
            'autorev-report = autobuilder.scripts.autorev_report:main'
        ]
    },
    install_requires=['buildbot>=0.9.14', 'boto3', 'twisted']
)
