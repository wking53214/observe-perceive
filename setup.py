from setuptools import setup, find_packages

setup(
    name="observe-perceive",
    version="1.0.0",
    description="Clinical governance system: OBSERVE (7-engine risk) + PERCEIVE (6-gate orchestrator)",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[],  # No external dependencies; stdlib only
)
