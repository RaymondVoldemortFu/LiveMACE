FROM python:3.10-slim

# Prevent Python from writing pyc files and ensure real-time output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# =============================
# Install minimal system dependencies
# Required for numpy / pandas / scipy / matplotlib / sklearn
# =============================
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gfortran \
    libopenblas-dev \
    liblapack-dev \
    libffi-dev \
    libstdc++6 \
    bash \
    coreutils \
    grep \
    sed \
    gawk \
    && rm -rf /var/lib/apt/lists/*

# =============================
# Install scientific Python packages
# =============================
RUN pip install --no-cache-dir \
    numpy \
    pandas \
    scipy \
    matplotlib \
    scikit-learn \
    statsmodels \
    sympy \
    seaborn

# Optional: common quant libraries
RUN pip install --no-cache-dir yfinance

# =============================
# Workspace directory
# =============================
WORKDIR /workspace

# =============================
# Default command keeps container alive
# =============================
CMD ["tail", "-f", "/dev/null"]
