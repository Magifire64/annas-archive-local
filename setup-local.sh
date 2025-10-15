#!/usr/bin/env bash

# Anna's [local] Archive Setup Script
# This script sets up Anna's [local] Archive with one command

set -e  # Exit on error

echo "============================================"
echo "Anna's [local] Archive Setup"
echo "============================================"
echo ""

# Check for Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Error: Docker is not installed."
    echo "Please install Docker from: https://docs.docker.com/get-docker/"
    exit 1
fi

# Check for Docker Compose
if ! docker compose version &> /dev/null; then
    echo "❌ Error: Docker Compose is not available."
    echo "Please ensure you have Docker Compose installed."
    exit 1
fi

echo "✓ Docker is installed"
echo ""

# Step 1: Set up environment files
echo "📝 Setting up environment files..."
if [ ! -f .env ]; then
    cp .env.dev .env
    echo "✓ Created .env from .env.dev"
else
    echo "ℹ .env already exists, skipping"
fi

if [ ! -f data-imports/.env-data-imports ]; then
    cp data-imports/.env-data-imports.dev data-imports/.env-data-imports
    echo "✓ Created data-imports/.env-data-imports"
else
    echo "ℹ data-imports/.env-data-imports already exists, skipping"
fi
echo ""

# Step 2: Build and start containers
echo "🔨 Building Docker containers (this may take several minutes)..."
docker compose build --quiet
echo "✓ Containers built"
echo ""

echo "🚀 Starting containers..."
docker compose up -d
echo "✓ Containers started"
echo ""

# Step 3: Wait for containers to be ready
echo "⏳ Waiting for services to be ready..."
echo "   This may take 2-3 minutes..."

# Wait for web container to be running
max_attempts=60
attempt=0
while ! docker compose ps web | grep -q "running"; do
    attempt=$((attempt + 1))
    if [ $attempt -ge $max_attempts ]; then
        echo "❌ Error: Web container failed to start in time"
        echo "Check logs with: docker compose logs web"
        exit 1
    fi
    sleep 5
    echo "   Still waiting... ($attempt/$max_attempts)"
done

# Additional wait for services to fully initialize
echo "   Waiting for services to initialize..."
sleep 30

echo "✓ Services are ready"
echo ""

# Step 4: Initialize database
echo "💾 Initializing database..."
if docker compose exec -T web flask cli dbreset; then
    echo "✓ Database initialized"
else
    echo "⚠ Database initialization had issues, but this might be normal on first run"
    echo "   If you experience problems, try running: ./run flask cli dbreset"
fi
echo ""

# Step 5: Final message
echo "============================================"
echo "✅ Setup Complete!"
echo "============================================"
echo ""
echo "Anna's [local] Archive is now running!"
echo ""
echo "🌐 Open your browser and visit:"
echo "   Main interface:     http://localtest.me:8000"
echo "   qBittorrent WebUI:  http://localtest.me:8080"
echo "     (Default login: admin / adminadmin)"
echo ""
echo "📚 Useful commands:"
echo "   View logs:        docker compose logs -f"
echo "   Stop services:    docker compose down"
echo "   Start services:   docker compose up -d"
echo "   Run Flask CLI:    ./run flask [command]"
echo ""
echo "📖 For more information, see README-LOCAL.md"
echo ""
