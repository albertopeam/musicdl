# musicdl

Download music from Soulseek at 320kbps MP3, organised by genre — using Spotify URLs as input.

> **Legal notice:** This tool does not circumvent any DRM or technical protection measure. Users are solely responsible for compliance with applicable copyright laws and the terms of service of any platform. Only download music you own or have the right to download. Using a VPN is recommended when connecting to Soulseek, as your IP address is visible to other peers.

---

## Features

- Input: plain text file with one Spotify URL per line (tracks, albums, playlists)
- Searches and downloads from Soulseek at 320kbps MP3
- Organises files into `music/{genre}/{subgenre}/{artist}/{album}/` directories
- Genre resolution from Last.fm → MusicBrainz → Beatport → fallback
- SQLite database tracks every download — re-runs skip already-downloaded tracks
- Writes ID3v2.4 tags (title, artist, album, year, genre, ISRC)

---

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- [sldl](https://github.com/fiso64/slsk-batchdl) binary (the Soulseek download engine)
- [Last.fm API key](https://www.last.fm/api/account/create) (free)

---

## Installation

### 1. Install `sldl`

`sldl` is **not on NuGet** — `dotnet tool install` will not work. Download the pre-built binary from GitHub releases instead.

#### macOS (Apple Silicon — M1/M2/M3/M4)

1. Go to **https://github.com/fiso64/slsk-batchdl/releases**
2. Download `sldl_osx-arm64.zip` and unzip it — you get a folder with `sldl` and `sldl.pdb` (ignore the `.pdb`)
3. Run these four commands in order:

```bash
# 1. Make executable (required — binary is not executable by default)
chmod +x ~/Downloads/sldl_osx-arm64/sldl

# 2. Ad-hoc sign it (required on Apple Silicon — macOS kills unsigned binaries)
sudo codesign --force --deep --sign - ~/Downloads/sldl_osx-arm64/sldl

# 3. Move to PATH
sudo mv ~/Downloads/sldl_osx-arm64/sldl /usr/local/bin/sldl

# 4. Verify
sldl --version
```

> **Why `codesign`?** macOS on Apple Silicon kills unsigned executables even after removing the quarantine flag. `--sign -` is ad-hoc signing — no Apple Developer account needed, it just marks the binary as intentionally allowed.

#### macOS (Intel)

Same steps but download `sldl_osx-x64.zip` instead of `sldl_osx-arm64.zip`.

#### Linux

```bash
chmod +x sldl
sudo mv sldl /usr/local/bin/sldl
sldl --version
```

### 2. Install musicdl

```bash
git clone https://github.com/youruser/music-downloader.git
cd music-downloader
uv sync
```

### 3. Configure

```bash
cp config.example.toml config.toml
cp .env.example .env
```

Edit `.env` and fill in your credentials:

```
MUSICDL_LASTFM_API_KEY=your_api_key
MUSICDL_LASTFM_API_SECRET=your_api_secret
MUSICDL_MB_USER_AGENT="musicdl/0.1 your@email.com"
```

Get a Last.fm API key at https://www.last.fm/api/account/create (free, instant).

---

## Usage

### Running commands

All commands are run via `uv run` because `musicdl` is installed inside the project's virtualenv, not globally:

```bash
uv run musicdl download urls.txt
uv run musicdl status
```

If you prefer to drop the `uv run` prefix, activate the virtualenv once per terminal session:

```bash
source .venv/bin/activate
musicdl status          # works without uv run while venv is active
deactivate              # exit the venv when done
```

---

### Create an input file

```
# urls.txt — one Spotify URL per line, # for comments
https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC
https://open.spotify.com/album/5ht7ItJgpBH7W6vJ3Tv4lE
https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
```

### Download

```bash
uv run musicdl download urls.txt
```

Output structure:

```
music/
├── electronic/
│   ├── deep house/
│   │   └── bicep/
│   │       └── bicep/
│   │           └── 01 - glue.mp3
│   └── techno/
│       └── charlotte de witte/
│           └── 01 - doppler.mp3
```

### Common options

```bash
# Preview metadata and genres without downloading
uv run musicdl download urls.txt --dry-run

# Retry tracks that previously failed with a hard error (timeout, sldl crash)
uv run musicdl download urls.txt --retry-failed

# Retry tracks not found on Soulseek without waiting for the 3-day cooldown
uv run musicdl download urls.txt --retry-not-found

# Re-download even if already present on disk
uv run musicdl download urls.txt --force

# Debug logging
uv run musicdl download urls.txt --verbose

# Custom output directory
uv run musicdl download urls.txt --output ~/Music/library
```

### Check status

```bash
# Show recent sessions and library counts per status
uv run musicdl status

# Reset all hard-failed tracks to pending (for manual retry)
uv run musicdl retry
```

### Track statuses explained

| Status | Meaning | What to do |
|---|---|---|
| `downloaded` | On disk, all good | Nothing |
| `not found` | Not on Soulseek yet | Auto-retried after 3 days, or force with `--retry-not-found` |
| `failed` | Hard error (timeout, connection) | Run `uv run musicdl retry` or `--retry-failed` |
| `pending` | Queued for next run | Will be picked up automatically |
| `skipped` | Already downloaded, skipped this session | Nothing |

### Extended mixes

By default musicdl prefers extended mixes over radio edits — important for DJ use. For each track it first searches for `"Artist - Title extended"` with a minimum length of 4.5 minutes. If no extended version is found on Soulseek, it falls back to the regular version.

To disable this behaviour or adjust the minimum length, edit `config.toml`:

```toml
[download]
prefer_extended = false          # set to false to always take whatever is available
min_extended_length_seconds = 270  # 270 = 4.5 min, increase for stricter filtering
```

### Generate config template

```bash
uv run musicdl init-config
```

---

## DJ Session Generator (coming soon)

A future `musicdl session` command will analyse your library for BPM, musical key, and energy level, then generate harmonically-compatible DJ set playlists as M3U files.

```bash
uv run musicdl analyze --all
uv run musicdl session --duration 60 --style electronic/techno --arc peak_hour
```

Install the optional audio analysis dependencies:

```bash
uv sync --extra session
```

---

## Development

```bash
# Run unit tests
uv run pytest tests/unit/ --cov=src/musicdl --cov-report=term-missing

# Run integration tests (mocked — no real sldl or network needed)
uv run pytest tests/integration/ -m integration -v

# Type checking
uv run pyright src/

# Format
uv run black src/ tests/
```

### Claude Code commands

If you develop this project with [Claude Code](https://claude.ai/code), the following slash commands are available:

| Command | When to use |
|---|---|
| `/test` | Run the unit suite and fix any failures |
| `/lint` | Run ruff + pyright across the codebase and fix all findings |
| `/review` | Review uncommitted changes before committing — checks correctness, architecture, types, and test coverage, and flags anything that should update the project tooling |
| `/improve` | End-of-feature retrospective — reads all rules, hooks, commands, and memory, then applies concrete improvements based on what was learned in the session |

**Typical workflow:**

```
code → /test → /lint → /review → commit → /improve (if something notable was learned)
```

`/improve` is not needed after every commit — run it when a session uncovered something worth capturing: an API change, a pattern that kept recurring, a mistake that a rule would have prevented.

---

## Configuration reference

| Config key | Env var override | Default | Description |
|---|---|---|---|
| — | `MUSICDL_LASTFM_API_KEY` | — | Last.fm API key (required) |
| — | `MUSICDL_LASTFM_API_SECRET` | — | Last.fm API secret (required) |
| — | `MUSICDL_MB_USER_AGENT` | — | MusicBrainz user agent (required) |
| `sldl.binary_path` | — | `sldl` | Path to sldl binary |
| `sldl.timeout_seconds` | — | `120` | Per-download timeout |
| `download.output_base` | — | `./music` | Base output directory |
| `download.staging_dir` | — | `./staging` | Temporary download dir |
| `download.max_retries` | — | `3` | Max retry attempts per track |
| `genre.cache_ttl_days` | — | `30` | Genre cache TTL in days |
| `database.path` | — | `./musicdl.db` | SQLite database path |

---

## License

MIT — see [LICENSE](LICENSE).
