FROM python:3.13-slim

WORKDIR /app

# Install system dependencies (needed for compiling some packages if any, plus timezone data)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential tzdata \
    && rm -rf /var/lib/apt/lists/*

# Set default timezone to IST (Asia/Kolkata) to fix datetime.now() offset
ENV TZ="Asia/Kolkata"
# Copy requirements
COPY requirements.txt /app/

# Install python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . /app/

# Create data directory for volume mounting
RUN mkdir -p /app/data

# Expose Gradio port
EXPOSE 7861

# Set the environment variables
ENV PYTHONUNBUFFERED=1
ENV HOST="0.0.0.0"

# Command to run the application
CMD ["python3", "app.py"]
