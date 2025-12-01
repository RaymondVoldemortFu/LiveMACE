FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install basic utilities if needed (optional, but helpful for "linux computer" feel)
# We keep it minimal as requested "only environment"
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    coreutils \
    grep \
    sed \
    gawk \
    && rm -rf /var/lib/apt/lists/*


# Set work directory
WORKDIR /workspace

# Default command (keep it running)
CMD ["tail", "-f", "/dev/null"]

