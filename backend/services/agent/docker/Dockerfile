FROM python:3.10-slim

# Prevent Python from writing pyc files and ensure real-time output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# =============================
# Install minimal system dependencies
# Required for numpy / pandas / scipy / matplotlib / sklearn
# =============================
# =============================
# Install minimal system dependencies
# Required for numpy / pandas / scipy / matplotlib / sklearn
# - Switch APT mirrors to Aliyun (mitigate deb.debian.org 502)
# - Works with both legacy sources.list and modern debian.sources
# =============================
RUN set -eux; \
        if [ -f /etc/apt/sources.list ]; then \
            sed -i \
                -e 's|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' \
                -e 's|https://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' \
                -e 's|http://security.debian.org/debian-security|https://mirrors.aliyun.com/debian-security|g' \
                -e 's|https://security.debian.org/debian-security|https://mirrors.aliyun.com/debian-security|g' \
                /etc/apt/sources.list; \
        fi; \
        if [ -d /etc/apt/sources.list.d ]; then \
            for f in /etc/apt/sources.list.d/*.sources; do \
                [ -e "$f" ] || continue; \
                sed -i \
                    -e 's|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' \
                    -e 's|https://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' \
                    -e 's|http://security.debian.org/debian-security|https://mirrors.aliyun.com/debian-security|g' \
                    -e 's|https://security.debian.org/debian-security|https://mirrors.aliyun.com/debian-security|g' \
                    "$f"; \
            done; \
        fi; \
        printf 'Acquire::Retries "8";\nAcquire::http::Timeout "30";\nAcquire::https::Timeout "30";\n' > /etc/apt/apt.conf.d/80retries; \
        apt-get update -y; \
        apt-get install -y --no-install-recommends --fix-missing \
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
                gawk; \
        rm -rf /var/lib/apt/lists/*

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
