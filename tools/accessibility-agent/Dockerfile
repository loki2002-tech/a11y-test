# Use the official Python 3.11 slim image as the base
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

# Set the working directory in the container
WORKDIR /app

# Install system dependencies required by Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject.toml first to leverage Docker cache for dependencies
COPY pyproject.toml /app/

# Install the Python dependencies directly using pip
# We also install playwright specifically so we can run its install commands
RUN pip install --no-cache-dir build \
    && pip install --no-cache-dir .

# Install Playwright browsers and their OS dependencies
# (We install chromium and firefox by default for accessibility testing)
RUN playwright install-deps chromium firefox
RUN playwright install chromium firefox

# Copy the rest of the application code
COPY src /app/src/

# Expose the port the API server will run on
EXPOSE 8000

# Create necessary directories that might be mounted as volumes
RUN mkdir -p /app/reports /app/evidence

# Command to run the FastAPI server
CMD ["python", "-m", "accessibility_agent.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
