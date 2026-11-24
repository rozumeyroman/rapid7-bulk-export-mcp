# Running the server in Docker for Claude Desktop (macOS)

The image is published to GitHub Container Registry for `linux/arm64` (Apple
Silicon) and `linux/amd64` on every push to `main`:

```
ghcr.io/rozumeyroman/rapid7-bulk-export-mcp:latest
```

Claude Desktop starts it as a child process over **stdio**. No port is opened,
so the HTTP transport's OIDC requirement does not apply.

Using Apple's own runtime instead of Docker? See
[claude-desktop-apple-container.md](./claude-desktop-apple-container.md).

## 1. Install a container runtime

Any of these works:

- **Colima** (free, open source): `brew install colima docker && colima start`
- **Docker Desktop** — free only for organisations under 250 employees and
  USD 10M revenue; larger companies need a paid subscription.
- **OrbStack** — free for personal use, paid licence for commercial use.

Check with `docker run --rm hello-world`.

With Colima, make it start at login: `brew services start colima`.

## 2. Add the server to Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "rapid7-bulk-export": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm", "--pull=always",
        "--read-only", "--tmpfs", "/tmp",
        "-e", "MCP_TRANSPORT=stdio",
        "-e", "RAPID7_API_KEY",
        "-e", "RAPID7_REGION",
        "-v", "rapid7-data:/data",
        "ghcr.io/rozumeyroman/rapid7-bulk-export-mcp:latest"
      ],
      "env": {
        "RAPID7_API_KEY": "<your Insight Platform API key>",
        "RAPID7_REGION": "eu"
      }
    }
  }
}
```

`-e RAPID7_API_KEY` without a value passes the variable from the `env` block,
so the key never appears in the process arguments. It is not baked into the
image.

If `docker` is not found, use the full path (`/opt/homebrew/bin/docker` with
Homebrew) — Claude Desktop does not read your shell `PATH`.

Restart Claude Desktop.

## 3. Updating

`--pull=always` fetches the newest `latest` image every time Claude Desktop
starts the server, so a push to `main` reaches the Mac on the next restart.

To pin a version instead, replace `latest` with a version tag (for example
`0.7.1`) or a commit tag (`sha-33ac99f`) and drop `--pull=always`.

## 4. Data

The DuckDB database with InsightVM exports lives in the Docker volume
`rapid7-data` and survives image updates.

```bash
docker volume inspect rapid7-data   # where it is
docker volume rm rapid7-data        # start over (deletes loaded exports)
```
