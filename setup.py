from setuptools import setup, find_packages

setup(
    name='LARS',
    version='0.1',
    packages=find_packages(include=['Scripts', 'Scripts.*']),

    install_requires=[
        'numpy',
        'pandas',
        'scipy'
    ],

)