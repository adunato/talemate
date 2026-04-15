# Changing host and port

## Backend

By default, the backend listens on `localhost:5050`.

There are two ways to change it:

1. Set the `TALEMATE_BACKEND_PORT` (and optionally `TALEMATE_BACKEND_HOST`) environment variables before launching Talemate. This is the easiest option and does not require editing any scripts.
2. Pass the `--host` and `--port` parameters explicitly on the command line. The CLI flags take precedence over the environment variables.

In either case, you also need to make sure the frontend knows the new backend address — see [Letting the frontend know about the new host and port](#letting-the-frontend-know-about-the-new-host-and-port) below.

### Using environment variables

#### :material-linux: Linux

```bash
TALEMATE_BACKEND_PORT=1234 ./start.sh
```

#### :material-microsoft-windows: Windows

```batch
set TALEMATE_BACKEND_PORT=1234
start.bat
```

### Using command-line flags

#### :material-linux: Linux

Copy `start.sh` to `start_custom.sh` and edit the `--host` and `--port` parameters.

```bash
#!/bin/sh
uv run src/talemate/server/run.py runserver --host 0.0.0.0 --port 1234
```

#### :material-microsoft-windows: Windows

Copy `start.bat` to `start_custom.bat` and edit the `--host` and `--port` parameters.

```batch
uv run src\talemate\server\run.py runserver --host 0.0.0.0 --port 1234
```

### Letting the frontend know about the new host and port

Copy `talemate_frontend/example.env.development.local` to `talemate_frontend/.env.production.local` and edit the `VITE_TALEMATE_BACKEND_WEBSOCKET_URL`.

```env
VITE_TALEMATE_BACKEND_WEBSOCKET_URL=ws://localhost:1234
```

Next rebuild the frontend.

```bash
cd talemate_frontend
npm run build
```

### Start the backend and frontend

Start the backend and frontend as usual.

#### :material-linux: Linux

```bash
./start_custom.sh
```

#### :material-microsoft-windows: Windows

```batch
start_custom.bat
```

## Frontend

By default, the frontend listens on `localhost:8082`.

There are two ways to change it:

1. Set the `TALEMATE_FRONTEND_PORT` (and optionally `TALEMATE_FRONTEND_HOST`) environment variables before launching Talemate. This is the easiest option and does not require editing any scripts.
2. Pass the `--frontend-host` and `--frontend-port` parameters explicitly on the command line. The CLI flags take precedence over the environment variables.

### Using environment variables

#### :material-linux: Linux

```bash
TALEMATE_FRONTEND_PORT=9090 ./start.sh
```

#### :material-microsoft-windows: Windows

```batch
set TALEMATE_FRONTEND_PORT=9090
start.bat
```

### Using command-line flags

#### :material-linux: Linux

Copy `start.sh` to `start_custom.sh` and edit the `--frontend-host` and `--frontend-port` parameters.

```bash
#!/bin/sh
uv run src/talemate/server/run.py runserver --host 0.0.0.0 --port 5055 \
--frontend-host localhost --frontend-port 9090
```

#### :material-microsoft-windows: Windows

Copy `start.bat` to `start_custom.bat` and edit the `--frontend-host` and `--frontend-port` parameters.

```batch
uv run src\talemate\server\run.py runserver --host 0.0.0.0 --port 5055 --frontend-host localhost --frontend-port 9090
```

### Start the backend and frontend

Start the backend and frontend as usual.

#### :material-linux: Linux

```bash
./start_custom.sh
```

#### :material-microsoft-windows: Windows

```batch
start_custom.bat
```

## Docker Runtime Configuration

For Docker deployments, you can configure the frontend port, backend port, and the WebSocket URL at container startup without rebuilding the image.

### Changing the frontend port

Set `TALEMATE_FRONTEND_PORT` before running `docker compose up`:

```bash
TALEMATE_FRONTEND_PORT=9090 docker compose up
```

This sets both the host-side published port and the port that uvicorn binds inside the container, so you can reach Talemate at `http://localhost:9090`.

### Changing the backend port

!!! warning "Always pair backend port changes with `VITE_TALEMATE_BACKEND_WEBSOCKET_URL`"
    The frontend's auto-detection assumes the backend lives on port `5050`. When you change `TALEMATE_BACKEND_PORT` you **must** also set `VITE_TALEMATE_BACKEND_WEBSOCKET_URL` to the matching URL, otherwise the browser will fail to connect to the backend.

```bash
TALEMATE_BACKEND_PORT=6060 \
VITE_TALEMATE_BACKEND_WEBSOCKET_URL=ws://localhost:6060/ws \
docker compose up
```

Replace `localhost` with the hostname your browser will use to reach the backend (e.g. a LAN IP or domain name).

### Setting WebSocket URL via Environment Variable

```yaml
# docker-compose.yml
services:
  talemate:
    environment:
      - VITE_TALEMATE_BACKEND_WEBSOCKET_URL=ws://your-backend-host:5050/ws
```

Or via command line:

```bash
VITE_TALEMATE_BACKEND_WEBSOCKET_URL=ws://192.168.1.100:5050/ws docker compose up
```

### Configuration Priority

The WebSocket URL is determined in this order:

1. **Runtime environment variable** (`VITE_TALEMATE_BACKEND_WEBSOCKET_URL` at container start)
2. **Auto-detection** (`ws://<current-browser-hostname>:5050/ws`)

This means you can use a single Docker image across different environments (staging, production) by simply changing the environment variable.