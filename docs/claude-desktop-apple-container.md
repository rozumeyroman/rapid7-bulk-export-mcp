# Running the server on Apple Container for Claude Desktop (macOS)

This is an alternative to [claude-desktop-docker.md](./claude-desktop-docker.md)
for Macs that use Apple's own container runtime,
[Apple Container](https://github.com/apple/container), instead of Docker,
Colima or OrbStack. It runs the same image:

```
ghcr.io/rozumeyroman/rapid7-bulk-export-mcp:latest
```

Claude Desktop starts the server over **stdio**, through a small wrapper
script. No port is opened, so the HTTP transport's OIDC requirement does not
apply.

Apple Container works differently from Docker in one way that shapes this
whole setup: **every `container run` is its own lightweight VM**, and a named
volume can be attached to only one VM at a time. Claude Desktop starts several
server processes at once (the chat client and the Cowork/Code pool), so the
Docker recipe of one `run -i --rm` per client does not work here. See
[Architecture](#6-architecture).

Tested with Apple Container 1.7.1 on macOS 27.2 (Apple Silicon), server 3.4.10.

## 1. Requirements

- A Mac with **Apple Silicon** (Apple Container does not run on Intel Macs).
- A recent macOS (Apple Container needs macOS 26 or later).
- **Apple Container**: download the signed installer `.pkg` from the
  [releases page](https://github.com/apple/container/releases) and check its
  signature before you install it:

  ```bash
  pkgutil --check-signature container-*-installer-signed.pkg
  ```

  The output should say `Status: signed by a developer certificate issued by
  Apple` with Apple as the developer. Then open the `.pkg`. The CLI is
  installed as `/usr/local/bin/container`.

- Start the service once by hand. The first start offers to install the
  default Linux kernel; accept it:

  ```bash
  container system start
  container system status   # status: running
  container --version
  ```

No Xcode, Homebrew or Python is needed on the Mac.

## 2. Start the container service at login

After a reboot the Apple Container service does not start by itself. A
LaunchAgent starts it at login. Save this as
`~/Library/LaunchAgents/local.container.system-start.plist` (replace
`<you>` with your user name):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>local.container.system-start</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/container</string>
        <string>system</string>
        <string>start</string>
        <string>--disable-kernel-install</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/<you>/Library/Logs/container-system-start.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/<you>/Library/Logs/container-system-start.log</string>
</dict>
</plist>
```

Load it and check:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.container.system-start.plist
launchctl print gui/$(id -u)/local.container.system-start | grep -E 'state|last exit'
tail ~/Library/Logs/container-system-start.log
```

`state = not running` with `last exit code = 0` is correct: the job runs
once at login and exits. `--disable-kernel-install` keeps it from waiting
for a prompt nobody will see. The wrapper also starts the service if it is
down, so the LaunchAgent only saves time on the first launch.

## 3. Store the API key in the Keychain

The wrapper reads the key from the macOS Keychain (service `rapid7-mcp`,
account `RAPID7_API_KEY`), so it is not written in plain text in the Claude
Desktop config. Run this in Terminal. It asks for the key twice and does not
echo it:

```bash
security add-generic-password -U -s rapid7-mcp -a RAPID7_API_KEY -w
```

`-U` updates an existing entry, so the same command rotates the key. To
check that the entry exists without printing it:

```bash
security find-generic-password -s rapid7-mcp -a RAPID7_API_KEY >/dev/null && echo ok
```

The first time the wrapper reads the key, macOS may ask whether `security`
may access the item. Choose **Always Allow**.

A `RAPID7_API_KEY` already set in the wrapper's environment wins over the
Keychain, which is handy for debugging with a test key.

## 4. The wrapper script

Save this as `~/.local/bin/rapid7-mcp` and make it executable
(`chmod +x ~/.local/bin/rapid7-mcp`). All paths are absolute because
Claude Desktop does not read your shell `PATH`.

```bash
#!/bin/bash
# Rapid7 Bulk Export MCP server (stdio) for Claude desktop, on Apple Container.
#
# One shared container ("rapid7-mcp") serves every Claude desktop client
# (chat, Cowork/Code pool). Each client gets its own server process inside it via
# `container exec`, so there is one VM, one kernel and one owner of the data volume.
#
# The container stops itself after RAPID7_MCP_IDLE_TIMEOUT seconds (default 900 = 15 min)
# without any MCP server process inside; the next client launch starts it again.
#
# stdout is reserved for MCP protocol messages only; all diagnostics go to stderr.
set -u

CONTAINER=/usr/local/bin/container
IMAGE=ghcr.io/rozumeyroman/rapid7-bulk-export-mcp:latest
NAME=rapid7-mcp
VOLUME=rapid7-data
LOCK="$HOME/Library/Caches/rapid7-mcp.lock"
IDLE_TIMEOUT="${RAPID7_MCP_IDLE_TIMEOUT:-900}"

# Idle watcher: the container's main process (PID 1 is --init). It checks every 30 s
# whether any MCP server process (run_server.py) is alive or a client has just claimed
# the container (/tmp/last-use touched < 60 s ago). After IDLE_TIMEOUT seconds of idling
# it raises /tmp/stopping, waits 2 s so a client that is claiming at that very moment
# can finish, re-checks, and exits; the container then stops and is removed (--rm).
# The pattern is written as run_server[.]py so this script's own cmdline never matches.
WATCHER='
IDLE=${IDLE_TIMEOUT:-900}; STEP=${IDLE_STEP:-30}; idle=0
busy() {
    for f in /proc/[0-9]*/cmdline; do
        grep -aq "run_server[.]py" "$f" 2>/dev/null && return 0
    done
    [ -f /tmp/last-use ] && [ $(( $(date +%s) - $(stat -c %Y /tmp/last-use) )) -lt 60 ]
}
while :; do
    sleep "$STEP"
    if busy; then idle=0; continue; fi
    idle=$((idle + STEP))
    [ "$idle" -lt "$IDLE" ] && continue
    mkdir /tmp/stopping 2>/dev/null
    sleep 2
    if busy; then rmdir /tmp/stopping 2>/dev/null; idle=0; continue; fi
    echo "rapid7-mcp: idle for ${IDLE}s, stopping" >&2
    exit 0
done
'

log() { echo "rapid7-mcp: $*" >&2; }
is_running() { "$CONTAINER" ls 2>/dev/null | awk -v n="$NAME" '$1==n {f=1} END {exit !f}'; }

# 0. API key comes from the macOS Keychain (service "rapid7-mcp", account "RAPID7_API_KEY").
#    A RAPID7_API_KEY already set in the environment takes precedence (handy for debugging).
#    Store or replace it with:
#      security add-generic-password -U -s rapid7-mcp -a RAPID7_API_KEY -w
if [ -z "${RAPID7_API_KEY:-}" ]; then
    if ! RAPID7_API_KEY=$(/usr/bin/security find-generic-password -s rapid7-mcp -a RAPID7_API_KEY -w 2>/dev/null) \
        || [ -z "$RAPID7_API_KEY" ]; then
        log "API key not found in Keychain (service rapid7-mcp, account RAPID7_API_KEY)"
        log "add it with: security add-generic-password -U -s rapid7-mcp -a RAPID7_API_KEY -w"
        exit 1
    fi
fi
export RAPID7_API_KEY

# 1. Start the container service if it is not running.
if ! "$CONTAINER" system status >/dev/null 2>&1; then
    log "starting container service..."
    "$CONTAINER" system start --disable-kernel-install 1>&2 </dev/null || log "container system start failed"
fi

# 2. Make sure the shared container is running. Clients start in parallel,
#    so creation is serialized with an atomic mkdir lock.
ensure_running() {
if ! is_running; then
    for _ in $(seq 1 120); do
        mkdir "$LOCK" 2>/dev/null && break
        # stale lock (crashed launcher) older than 2 minutes
        if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +2 2>/dev/null)" ]; then rmdir "$LOCK" 2>/dev/null; fi
        sleep 0.5
    done
    trap 'rmdir "$LOCK" 2>/dev/null' EXIT
    if ! is_running; then
        # Refresh the image only when (re)creating the container; offline is fine.
        "$CONTAINER" image pull --progress none "$IMAGE" >/dev/null 2>&1 </dev/null || \
            log "image pull failed (offline?), using cached image"

        # First run only: create the volume and hand it to the image user (uid 1001).
        if ! "$CONTAINER" volume inspect "$VOLUME" >/dev/null 2>&1; then
            log "initializing volume $VOLUME"
            "$CONTAINER" volume create "$VOLUME" >/dev/null 2>&1
            "$CONTAINER" run --rm --progress none --user 0 --entrypoint sh \
                -v "$VOLUME":/data "$IMAGE" -c 'chown 1001:0 /data && chmod 0770 /data' 1>&2 </dev/null || \
                log "volume init failed"
        fi

        # Remove a stopped leftover with the same name, then start the container
        # with the idle watcher as its main process.
        "$CONTAINER" rm "$NAME" >/dev/null 2>&1
        log "starting shared container $NAME (idle timeout ${IDLE_TIMEOUT}s)"
        "$CONTAINER" run -d --rm --name "$NAME" --progress none --init \
            --read-only --tmpfs /tmp \
            --cap-drop ALL \
            -v "$VOLUME":/data \
            -e IDLE_TIMEOUT="$IDLE_TIMEOUT" \
            --entrypoint sh "$IMAGE" -c "$WATCHER" >/dev/null </dev/null || log "failed to start $NAME"
    fi
    rmdir "$LOCK" 2>/dev/null; trap - EXIT
fi
}

# 2b. Claim the container so the idle watcher will not stop it under us. If it is
#     already shutting down (/tmp/stopping exists) or the exec fails, wait for it to
#     go away and start a fresh one.
claim() {
    "$CONTAINER" exec "$NAME" sh -c '[ ! -d /tmp/stopping ] && touch /tmp/last-use' \
        </dev/null >/dev/null 2>&1
}
for attempt in 1 2 3; do
    ensure_running
    claim && break
    log "container $NAME is stopping or unavailable, retrying ($attempt/3)"
    for _ in $(seq 1 60); do is_running || break; sleep 0.5; done
done

# 3. Hand over stdin/stdout to a fresh server process inside the shared container.
#    The API key is passed to this process only, not to the container itself.
exec "$CONTAINER" exec -i -w /app \
    -e MCP_TRANSPORT=stdio \
    -e RAPID7_API_KEY \
    -e RAPID7_REGION \
    "$NAME" uv run --no-sync run_server.py /data/rapid7_bulk_export.db
```

What each step does:

| Step | What happens | Why |
|---|---|---|
| 0. Key | Takes `RAPID7_API_KEY` from the environment, otherwise from the Keychain. Exits with code 1 and a hint in the MCP log if neither has it | The key stays out of `claude_desktop_config.json` |
| 1. Service | Runs `container system start` if `container system status` fails | Covers a Mac where the LaunchAgent has not run or the service was stopped |
| 2. `ensure_running` | If the `rapid7-mcp` container is not running, takes an atomic `mkdir` lock, pulls the image, creates and prepares the volume on first use, and starts the container in the background | Clients start in parallel; the lock makes sure only one of them creates the container. A lock older than 2 minutes is treated as left over from a crash |
| Volume init | `chown 1001:0 /data && chmod 0770 /data` in a one-off root container | A new Apple Container volume is owned by root, while the image runs as uid 1001 (Docker copies the owner from the image, Apple Container does not) |
| Container | `--init --read-only --tmpfs /tmp --cap-drop ALL`, volume at `/data`, main process is the idle watcher | Same hardening as the Docker recipe, plus dropped capabilities. The key is **not** passed here, so it is not in PID 1's environment |
| Idle watcher | Every 30 s checks for a `run_server.py` process or a recent claim. After `IDLE_TIMEOUT` (default 900 s) without either, raises `/tmp/stopping`, waits 2 s, checks again and exits. The container stops and is removed (`--rm`); the data stays in the volume | The VM does not keep running (and holding its memory) after Claude Desktop is closed |
| 2b. `claim` | Touches `/tmp/last-use` unless `/tmp/stopping` exists. On failure waits up to 30 s for the old container to go away and starts a new one, up to 3 attempts | A client that starts at the moment the watcher is shutting down never lands in a dying container |
| 3. `exec` | Replaces the wrapper with `container exec -i` of a new server process, passing `MCP_TRANSPORT`, `RAPID7_API_KEY` and `RAPID7_REGION` | Claude Desktop talks stdio straight to the server. `-e NAME` without a value passes the variable from the wrapper's environment, so the key is not in the process arguments |

stdout carries MCP messages only; every diagnostic line goes to stderr, which
Claude Desktop writes to its MCP log.

## 5. Add the server to Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(back it up first):

```json
{
  "mcpServers": {
    "rapid7-bulk-export": {
      "command": "/Users/<you>/.local/bin/rapid7-mcp",
      "env": {
        "RAPID7_REGION": "eu"
      }
    }
  }
}
```

There are no `args`: the wrapper holds the container settings. The key comes
from the Keychain. If you prefer not to use the Keychain, add
`"RAPID7_API_KEY": "<your Insight Platform API key>"` to `env`, keeping in
mind that the key is then stored in plain text on disk.

Optional: `"RAPID7_MCP_IDLE_TIMEOUT": "1800"` in `env` changes the idle
timeout. It takes effect the next time the container is created.

Restart Claude Desktop.

## 6. Architecture

```
Claude Desktop (/Applications/Claude.app)
 ├─ chat client                 ─┐   each client starts
 └─ Cowork/Code pool client     ─┤   ~/.local/bin/rapid7-mcp over stdio
                                 ▼
          ~/.local/bin/rapid7-mcp  (bash wrapper on the Mac)
            0. key from the Keychain
            1. start the Apple Container service if needed
            2. no "rapid7-mcp" container? under a mkdir lock:
               pull image → (first run) create + chown volume → container run -d
            2b. claim the container (touch /tmp/last-use)
            3. exec container exec -i rapid7-mcp uv run ... run_server.py
                                 ▼
   Apple Container: ONE lightweight VM "rapid7-mcp" (1 GiB, 4 CPU, own Linux kernel)
     PID 1: init → idle watcher (sh)   --read-only, --tmpfs /tmp, --cap-drop ALL
     server process #1 (exec, chat client)         ─┐  DuckDB, short connections,
     server process #2 (exec, Cowork/Code client)  ─┤  retry on file lock
                                                    ▼
     /data  ←  named volume rapid7-data (ext4 image, /data owned by uid 1001)
               rapid7_bulk_export.db, rapid7_bulk_export_tracking.db (DuckDB), imports/
```

Why one long-lived container plus `container exec`, instead of a container
per client:

- In Apple Container every `container run` boots a separate VM. A named
  volume is an ext4 disk image, and only **one** VM can attach it. When Claude
  Desktop started a second server with its own `container run`, that start
  failed with `VZErrorDomain Code=2 "The storage device attachment is
  invalid."` and Claude Desktop showed "Connection closed".
- A folder shared from the Mac (bind mount over virtiofs) avoids the attach
  error, but DuckDB's file locks are not reliable across several VMs on
  virtiofs. With every server process in one Linux kernel on a local ext4
  volume, the locks work as DuckDB expects.
- The server opens short DuckDB connections and retries when the file is
  locked (`connect_with_retry` in `src/db_utils.py`: up to 5 attempts,
  about 1.5 s in total), so several processes can share one database.

Measured on the setup above:

| Scenario | Time to the `initialize` reply |
|---|---|
| Container already running | ~0.5 s |
| Cold start (pull + create container) | ~2.1 s |
| 3 clients at once from cold | 2.2–2.7 s, one container created |

Memory inside the container is about 255 MB with one client and about
335 MB with two.

## 7. Data

The DuckDB databases and imported Parquet files live in the named volume
`rapid7-data`, which survives container restarts, idle stops and image
updates:

```bash
container volume inspect rapid7-data
# source: ~/Library/Application Support/com.apple.container/volumes/rapid7-data/volume.img
container exec rapid7-mcp sh -c 'ls -la /data'
```

To start over (this deletes all loaded exports), quit Claude Desktop, then:

```bash
container stop rapid7-mcp
container volume rm rapid7-data
```

The wrapper creates and prepares a new volume on the next launch.

## 8. Updating

The wrapper pulls `latest` only when it creates the container, so reconnects
stay fast. A new image is picked up the next time the container is created:
after the idle stop, after a reboot, or right away with:

```bash
container stop rapid7-mcp   # with Claude Desktop closed, or restart it afterwards
```

To pin a version, replace `latest` in `IMAGE` with a version tag (for
example `0.7.1`) or a commit tag (`sha-33ac99f`).

## 9. Memory and DuckDB

> **Known risk, not tuned by default.** By default `DUCKDB_MEMORY_LIMIT` is
> `4GB` and `DUCKDB_THREADS` is the CPU count (4 here). As the
> [configuration table](../README.md) says, peak memory runs from about 1.8×
> that limit (2 threads) to about 3.4× it, and the limit should be about a
> third of the memory available to the container. The container gets Apple
> Container's default **1 GiB**, shared by every server process inside it.
> A large export load can therefore run out of memory.

Ways to fix it (pick one or combine):

- Give the VM more memory: add `-m 4G` (and optionally `-c 4`) to the
  `container run -d` line in the wrapper, then `container stop rapid7-mcp`
  so it is recreated.
- Make DuckDB fit the VM: add `-e DUCKDB_MEMORY_LIMIT=300MB -e
  DUCKDB_THREADS=2` to the final `container exec` line (size the limit to
  about a third of the VM memory). `DUCKDB_THREADS=2` roughly halves peak
  memory for about 30% more load time.

Separately: while one process holds a long write (a large load), another
client's call fails with a lock error after its retries run out. That is
how DuckDB works with one writer, not a fault in this setup; retry the call
after the load finishes.

## 10. Troubleshooting

Logs:

| What | Where |
|---|---|
| This server's MCP log (wrapper messages and server stderr) | `~/Library/Logs/Claude/mcp-server-rapid7-bulk-export.log` |
| All MCP servers | `~/Library/Logs/Claude/mcp.log` |
| LaunchAgent | `~/Library/Logs/container-system-start.log` |
| Container state | `container ls -a`, `container stats --no-stream`, `container logs rapid7-mcp` |

Common errors:

- **`VZErrorDomain Code=2 "The storage device attachment is invalid."`** —
  a second VM tried to attach `rapid7-data`. Something started its own
  `container run -v rapid7-data:/data` (for example the Docker recipe
  adapted to `container`) while the shared container was up. Use the
  wrapper for every client, and check `container ls -a` for stray
  containers.
- **`PermissionError: [Errno 13] Permission denied: '/data/imports'`** —
  the volume is still owned by root. The wrapper fixes this only when it
  creates the volume; for a volume made by hand, run once:

  ```bash
  container run --rm --user 0 --entrypoint sh -v rapid7-data:/data \
    ghcr.io/rozumeyroman/rapid7-bulk-export-mcp:latest \
    -c 'chown 1001:0 /data && chmod 0770 /data'
  ```

  (stop `rapid7-mcp` first, because only one VM can attach the volume).
- **"Connection closed" in Claude Desktop** — the wrapper or the server
  exited. Look for `rapid7-mcp:` lines in the MCP log: a missing Keychain
  entry, a failed `container system start`, or the volume attach error
  above. Run the wrapper by hand in Terminal to see its stderr:
  `~/.local/bin/rapid7-mcp </dev/null`.
- **`API key not found in Keychain`** — add the key as in
  [section 3](#3-store-the-api-key-in-the-keychain).
- **Anything on stdout that is not JSON** breaks the MCP session. If you
  change the wrapper, send every diagnostic to stderr.

## 11. Differences from the Docker recipe

| Aspect | [Docker recipe](./claude-desktop-docker.md) | Apple Container (this guide) |
|---|---|---|
| Runtime | Colima, Docker Desktop or OrbStack | Apple Container |
| `command` in the config | `docker` with an `args` array | the wrapper `~/.local/bin/rapid7-mcp`, no `args` |
| Process model | a separate `docker run -i --rm` per client; all containers share one VM, so a shared volume is fine | one long-lived container, a `container exec` per client |
| Container lifetime | removed when the client exits | stops itself after 15 minutes without a server process |
| Image updates | `--pull=always` on every start | `pull` only when the container is created |
| Hardening | `--read-only --tmpfs /tmp` (compose adds `no-new-privileges`) | `--read-only --tmpfs /tmp --cap-drop ALL --init`; `container run` has no `no-new-privileges` flag |
| Volume | Docker volume, owner copied from the image | ext4 image, needs a one-time `chown` to uid 1001 |
| Runtime at login | `brew services start colima` | LaunchAgent `local.container.system-start` |
| API key | `env` block in the config | macOS Keychain, read by the wrapper |
