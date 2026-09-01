from setuptools import find_packages, setup

from price_bell import __version__


setup(
    name="price-bell",
    version=__version__,
    description="Configurable A-share price alerts via ServerChan and ntfy",
    packages=find_packages(),
    python_requires=">=3.6",
    entry_points={"console_scripts": ["price-bell=price_bell.__main__:main"]},
    license="MIT",
)
