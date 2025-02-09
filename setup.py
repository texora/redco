#  Copyright 2021 Google LLC
#  #
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#  #
#      https://www.apache.org/licenses/LICENSE-2.0
#  #
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

from setuptools import find_packages, setup


setup(
    name="redco",
    version="0.4.22",
    author="Bowen Tan",
    packages=find_packages(),
    install_requires=['jax', 'flax', 'optax'],
    include_package_data=True,
    python_requires=">=3.8",
    long_description=open('README.md').read(),
    long_description_content_type="text/markdown",
    url='https://github.com/tanyuqian/redco'
)