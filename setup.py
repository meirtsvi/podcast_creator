from setuptools import setup, find_packages

setup(
    name="podcast_creator",
    version="1.0.0",
    description="Personal podcast generation tools",
    author="Meir Tsvi",
    author_email="meir.tsvi@live.com",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires="==3.11.14",
    data_files=[('', ['*.txt', '*.csv' ])],
)