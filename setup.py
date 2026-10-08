from setuptools import find_packages, setup

with open("requirements.txt") as f:
    install_requires = f.read().strip().split("\n")

# Read version from inner module __init__.py without importing it
# (importing would require frappe at install time).
import re
with open("floatra_credit/__init__.py") as f:
    match = re.search(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", f.read(), re.M)
    version = match.group(1) if match else "0.1.0"

setup(
    name="floatra_credit",
    version=version,
    description=(
        "Floatra inventory financing for ERPNext — Mode A native connector "
        "(equivalent to the Odoo addon)."
    ),
    author="Floatra",
    author_email="engineering@floatra.com",
    license="MIT",
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
    install_requires=install_requires,
)
