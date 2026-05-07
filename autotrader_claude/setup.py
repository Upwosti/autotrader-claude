from setuptools import setup, find_packages

setup(
    name="autotrader-claude",
    version="1.1.0",
    description="Evolutionary ICT trading strategy system powered by Claude AI",
    packages=find_packages(),
    python_requires=">=3.11",
    install_requires=[
        "vectorbt>=0.26.2",
        "pandas>=2.2.2",
        "numpy>=1.26.4",
        "supabase>=2.4.6",
        "anthropic>=0.28.0",
        "python-telegram-bot>=21.4",
        "flask>=3.0.3",
        "loguru>=0.7.2",
        "requests>=2.32.3",
        "python-dotenv>=1.0.1",
        "yfinance>=0.2.40",
        "scipy>=1.13.1",
    ],
    entry_points={
        "console_scripts": [
            "autotrader=main:main",
        ],
    },
)
