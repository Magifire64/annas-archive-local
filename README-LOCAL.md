# Anna's [local] Archive

Welcome to **Anna's [local] Archive** - a local version of Anna's Archive that you can run on your own computer to browse and access torrents you've downloaded.

## Quick Start (One-liner)

### Prerequisites
- Docker (with Docker Compose)
- Git
- At least 4GB of RAM
- At least 10GB of free disk space

### Installation

```bash
git clone https://github.com/Magifire64/annas-archive-local.git && cd annas-archive-local && ./setup-local.sh
```

This will:
1. Set up the necessary environment files
2. Build and start all required containers
3. Initialize the databases
4. Set up the local archive interface

After setup completes (approximately 5-10 minutes), visit [http://localtest.me:8000](http://localtest.me:8000)

## Manual Setup

If you prefer to set up step by step:

```bash
# 1. Clone the repository
git clone https://github.com/Magifire64/annas-archive-local.git
cd annas-archive-local

# 2. Configure environment
cp .env.dev .env
cp data-imports/.env-data-imports.dev data-imports/.env-data-imports

# 3. Build and start containers
docker compose up --build -d

# 4. Initialize database (wait for containers to be ready first)
./run flask cli dbreset

# 5. Access the application
# Open http://localtest.me:8000 in your browser
```

## Features

### Torrent Management
- **Browse available torrents**: View all torrents from Anna's Archive
- **Download torrents**: Select and download entire collections
- **External torrent client support**: Point to your existing torrent downloads

### Local File Management
- **Automatic indexing**: Files are indexed when torrents complete
- **Manual indexing**: Index your existing file collections
- **Fast search**: No MD5 computation needed - indexes by filename and location

### Search & Discovery
- **Local files filter**: Special search option to find only files you have locally
- **Local download button**: Direct downloads from your local archive
- **Archive extraction**: Download individual files from within archives

### Metadata Management
- **One-click metadata download**: Get the latest aa_derived_mirror_metadata
- **Easy database loading**: Load metadata into local databases with one click
- **Progress tracking**: See real-time progress of data imports

## Configuration

### Torrent Client Integration
To use an existing torrent directory:

```bash
# Edit docker-compose.yml to add your torrent directory
volumes:
  - "/path/to/your/torrents:/torrents"
```

### Disk Space Management
Configure available disk space in the web interface at Settings > Storage.

## Architecture

Anna's [local] Archive includes:
- **Web Interface**: Flask-based application for browsing and searching
- **Databases**: 
  - Elasticsearch for search indexing
  - MariaDB for metadata and file locations
- **Torrent Client**: Integrated qBittorrent with Web UI (accessible at http://localtest.me:8080)
  - Default credentials: admin / adminadmin (change on first login)
- **File Indexer**: Automatic and manual file indexing service
- **Archive Proxy**: Direct extraction from archive files

## Differences from Main Anna's Archive

Anna's [local] Archive is optimized for local use:
- **Simplified**: Only includes features relevant for local browsing
- **Efficient**: No unnecessary external connections
- **Self-contained**: Works entirely offline once set up
- **Lightweight**: Reduced resource requirements

Pages that require external connections or cloud infrastructure have been disabled.

## Troubleshooting

### Containers won't start
Check Docker resource allocation:
- Memory limit: at least 8GB recommended
- Swap: at least 4GB
- Disk space: ensure at least 10% free space

### ElasticSearch permission issues
```bash
sudo chmod 0777 -R ../allthethings-elastic-data/ ../allthethings-elasticsearchaux-data/
```

### MariaDB memory consumption
Comment out `key_buffer_size` in `mariadb-conf/my.cnf`

## Contributing

Issues and improvements welcome! Please file issues at the main repository.

## License

Released in the public domain under the terms of CC0. See LICENSE file for details.
